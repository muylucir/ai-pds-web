# backend/aipds/routes/projects.py
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from aipds import app as app_module
from aipds import error_codes as ec
from aipds.parsers.state import parse_state_file
from aipds.project_store import write_manifest, delete_project_data
from aipds.proto.cleanup import purge_project_prototypes

_log = logging.getLogger(__name__)

router = APIRouter()

_STATE_PATH = "aiplc-docs/aiplc-state.md"


async def _progress(pid: str) -> dict | None:
    """Progress for the projects on this page. Read straight from S3, bypassing
    ensure_workspace (listing must not trigger lazy initialisation of N workspaces).
    Fail-soft: any failure degrades to None and never blocks the list response."""
    if not app_module.durable_projects_enabled():
        return None
    try:
        md = await app_module.s3_store_factory(pid).get(_STATE_PATH)
        state = parse_state_file(md)
    except Exception:
        return None
    if not state.stages:          # file present but no stages parsed -> nothing to show
        return None
    return {
        "current_stage": state.current_stage,
        "completed": sum(1 for s in state.stages if s.status == "completed"),
        "total": len(state.stages),
    }

class CreateProject(BaseModel):
    project_id: str
    name: str | None = None
    # The Bedrock model id this project will use. Unset means it runs on the env
    # default (app.project_model's fallback chain).
    model_id: str | None = None
    # This project's output language ("ko"|"en"). Unset means it runs as "ko".
    # Separate from the UI language (the aipds_lang cookie): this one is the language
    # of the documents, the prototype and the chat, decided once at creation.
    language: str | None = None


async def _validate_model_id(model_id: str | None) -> None:
    """Check membership of the catalog's **displayed list**.

    The displayed list rather than the registered list: a model with display off was
    deliberately taken down by an admin, so a new project must not be able to choose
    it.

    Without this check an arbitrary string lands in the manifest, and the failure
    surfaces on the first conversation turn as AccessDenied (outside the IAM wildcard)
    or ValidationException (a profile that does not exist) -- both of which appear only
    in the backend log.
    """
    if model_id is None:
        return
    allowed = {e.model_id for e in await app_module.model_catalog().displayed()}
    if model_id not in allowed:
        raise HTTPException(status_code=400,
                            detail=ec.MODEL_NOT_SELECTABLE)


#: The permitted output languages. Must be the same set as
#: ProjectRegistry._LANGUAGES: a value allowed through here that then hits that
#: fallback means the language the user chose is silently ignored.
_LANGUAGES = ("ko", "en")


def _validate_language(language: str | None) -> None:
    """Only two values are allowed.

    An arbitrary string in the manifest leaves place_rules unable to decide which
    directive block to prepend, and ProjectRegistry.get_language drops it to "ko" --
    which means the language the user chose is silently ignored. Blocking it at
    creation time is the only place that removes that silence.
    """
    if language is None:
        return
    if language not in _LANGUAGES:
        raise HTTPException(status_code=400,
                            detail=ec.LANGUAGE_UNSUPPORTED)


@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    # Validate before building the workspace -- creating a local directory and a
    # runner only to undo them for a request we were going to reject is waste.
    await _validate_model_id(body.model_id)
    _validate_language(body.language)
    # Settled here so the manifest and the registry share one created_at -- the
    # list's sort key (ascending by creation date) then does not change across a
    # restart.
    created_at = datetime.now(timezone.utc).isoformat()
    # **register has to come before make_workspace.** The order is load-bearing.
    #
    # make_workspace calls driver_factory, and that factory **reads the registry**
    # through project_language(pid) and project_model(pid) to assemble the driver.
    # make_workspace used to come first, so those reads saw a project that was not
    # registered yet and picked up the fallbacks -- "ko" for the language, the env
    # default for the model. And the driver built that way is held by the attached
    # Workspace for the life of the process, so **every turn** of a freshly created
    # English project ran in Korean (measured 2026-08-04).
    #
    # This is why the symptom was confusing: the manifest, the registry and the header
    # badge all get "en" correctly. The only thing out of step is the driver, so the
    # screen looks English while only the conversation is Korean. The model side is
    # quieter still -- the env fallback means it runs on the deployment's default model
    # rather than the chosen one, with no error and nothing in the log.
    #
    # Moving the registration earlier adds one more thing to undo on failure (the
    # registry.remove in the except below). In exchange, the order matches the fact
    # that the driver reads the registry. Making the factory take the values as
    # arguments is an alternative, but that changes the signatures of driver_factory,
    # make_workspace and the restore path, and the lazy-initialisation path
    # (deps.ensure_workspace) would still read the registry. Leaving the reader alone
    # and fixing the order is the narrower change.
    app_module.registry.register(body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id,
                                 language=body.language)
    try:
        workspace = await app_module.make_workspace(body.project_id)
    except Exception:
        # Never leave a registration without a workspace -- that state is a project
        # that appears in the list but cannot be opened.
        app_module.registry.remove(body.project_id)
        raise
    if app_module.durable_projects_enabled():
        try:
            await write_manifest(app_module.projects_root_s3_factory(),
                                 body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id,
                                 language=body.language)
        except Exception:
            # Spec decision: never quietly create a project that a restart would
            # make disappear.
            _log.exception("manifest write failed for %s", body.project_id)
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("workspace cleanup after manifest failure failed")
            # Undo the registration too (needed now that register comes first).
            app_module.registry.remove(body.project_id)
            raise HTTPException(status_code=500, detail="project persistence failed")
    app_module.registry.attach(body.project_id, workspace)
    return {"project_id": body.project_id, "name": body.name,
            "model_id": body.model_id,
            # Return the language it will actually run in (unset -> "ko").
            # Returning null would make the frontend learn the fallback rule too.
            "language": app_module.registry.get_language(body.project_id)}

@router.get("/projects")
async def list_projects(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=50)):
    # A page of the list plus progress read from S3 (fail-soft). It does not trigger
    # lazy workspace initialisation -- durable project metadata (DynamoDB) is a later
    # production concern.
    ids = app_module.registry.list_ids()
    total = len(ids)
    page_ids = ids[(page - 1) * size : page * size]
    progresses = await asyncio.gather(*(_progress(pid) for pid in page_ids))
    return {
        "projects": [
            {"project_id": pid, "name": app_module.registry.get_name(pid),
             "created_at": app_module.registry.get_created_at(pid),
             "model_id": app_module.registry.get_model_id(pid),
             "language": app_module.registry.get_language(pid),
             "progress": prog}
            for pid, prog in zip(page_ids, progresses)
        ],
        "total": total,
        "page": page,
        "size": size,
    }

@router.get("/projects/{pid}")
async def get_project(pid: str):
    """Metadata for one project. What the model badge in the header calls.

    It reads the registry only, bypassing ensure_workspace -- one badge must not
    trigger lazy workspace initialisation (booting a runner). list_projects's
    _progress reads S3 directly for the same reason.
    """
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    return {"project_id": pid,
            "name": app_module.registry.get_name(pid),
            "created_at": app_module.registry.get_created_at(pid),
            "model_id": app_module.registry.get_model_id(pid),
            "language": app_module.registry.get_language(pid)}

@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    """Delete everything (spec decision): stop the runner (best-effort) -> clean up the
    prototype's real substance (500 on failure) -> delete the S3 session and artifacts
    (500 on failure, idempotent retry) -> remove from the registry."""
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    already_stopped = None
    if app_module.registry.has_workspace(pid):
        already_stopped = app_module.registry.get(pid)
        try:
            await already_stopped.runner.stop()
        except Exception:
            _log.exception("runner stop failed for %s during delete (continuing)", pid)
    # A prototype's **real substance** lives outside the S3 prefix: the local build
    # tree, the running preview process and its port, the access token (file plus an
    # in-memory cache), the build session, and the survey token index at the bucket
    # **root** (surveys/by-token/). delete_project_data below removes only
    # projects/{pid}/ and sessions/session_{pid}/, so all of that used to survive --
    # and a surviving token in particular means an already-shared preview link keeps
    # opening for a deleted project (the proxy does not look at project registration,
    # only at proto_host's token and state).
    #
    # It has to run **before the S3 deletion.** The survey token index has no reverse
    # lookup and can only be reclaimed by reading the question files, so deleting the
    # prefix first leaves that index permanently unreachable by any code (see
    # SurveyStore.purge). That is why a failure stops here with a 500: leaving the
    # registry and S3 intact is what makes a retry meaningful (every step is
    # idempotent).
    durable = app_module.durable_projects_enabled()
    failures = await purge_project_prototypes(
        pid,
        host=app_module.proto_host(),
        sessions=app_module.proto_sessions,
        s3=app_module.s3_store_factory(pid) if durable else None,
        survey_store_factory=app_module.survey_store_factory if durable else None,
    )
    if failures:
        _log.error("prototype cleanup failed for %s: %s", pid, ",".join(failures))
        raise HTTPException(status_code=500,
                            detail=f"prototype cleanup failed: {','.join(failures)}")
    # The workspace directory is removed **by path**. `runner.stop()` above also
    # has an rmtree, but it sits behind the `has_workspace(pid)` gate, and that flag
    # is False for **every project not opened since the restart** because boot-time
    # restore only calls `register()` (the measurement is in
    # app.purge_local_workspace's docstring). And stop's failures are swallowed, so a
    # failing driver shutdown skips the rmtree with it.
    #
    # Placed before the S3 deletion so a failure stops with a 500: answering "deleted"
    # while the documents are still on local disk breaks the promise made to the
    # user.
    try:
        await app_module.purge_local_workspace(pid)
    except Exception:
        _log.exception("workspace purge failed for %s", pid)
        raise HTTPException(status_code=500, detail="workspace purge failed")
    if app_module.durable_projects_enabled():
        try:
            await delete_project_data(app_module.session_s3_factory(),
                                      app_module.projects_root_s3_factory(), pid)
        except Exception:
            _log.exception("S3 delete failed for %s", pid)
            raise HTTPException(status_code=500, detail="project delete failed")
    removed = app_module.registry.remove(pid)
    # Guard against the reverse race: even if has_workspace was False and the stop
    # block above was skipped, a concurrent ensure_workspace may have finished booting
    # and attached while we awaited the S3 deletion. The workspace returned by the
    # final remove is the cleanup point -- if it is the same object already stopped
    # above (the normal, race-free path) a duplicate stop is avoided, and if it is a
    # different object (a workspace attached late) it is stopped so the VM does not
    # leak.
    if removed is not None and removed is not already_stopped:
        try:
            await removed.runner.stop()
        except Exception:
            _log.exception("runner stop failed for %s during final registry removal (continuing)", pid)
    return {"deleted": True}
