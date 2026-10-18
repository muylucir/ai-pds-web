# backend/aipds/routes/projects.py
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from aipds import app as app_module
from aipds import error_codes as ec
from aipds import project_access
from aipds.auth.deps import require_user
from aipds.auth.models import Principal
from aipds.parsers.state import parse_state_file
from aipds.project_store import write_manifest, delete_project_data
from aipds.proto.cleanup import purge_project_prototypes
from aipds.proto.hosting import prototype_store

_log = logging.getLogger(__name__)

router = APIRouter()

_STATE_PATH = "aiplc-docs/aiplc-state.md"


async def _progress(pid: str) -> dict | None:
    """Progress for a project on the page. Reads S3 directly -- does not go through
    ensure_workspace (a list query must not trigger lazy initialization of N workspaces).
    fail-soft: any failure degrades to None and never blocks the list response."""
    if not app_module.durable_projects_enabled():
        return None
    try:
        md = await app_module.s3_store_factory(pid).get(_STATE_PATH)
        state = parse_state_file(md)
    except Exception:
        return None
    if not state.stages:          # The file exists but if stage parsing yields nothing, there is nothing to show
        return None
    # Count in the same unit as the sidebar (6 capabilities) -- ones this path skips are left out of the denominator.
    caps = [c for c in state.capabilities if c.status != "not_applicable"]
    current = next((c.name for c in caps if c.status == "in_progress"), None)
    return {
        "current_stage": current,
        "completed": sum(1 for c in caps if c.status == "completed"),
        "total": len(caps),
    }

class CreateProject(BaseModel):
    project_id: str
    name: str | None = None
    # The Bedrock model id this project uses. If unset, it runs on the env default
    # (the fallback chain in app.project_model).
    model_id: str | None = None
    # This project's output language ("ko"|"en"). If unset, it runs as "ko".
    # Separate from the UI language (aipds_lang cookie) -- this is the language of the
    # documents, prototype, and chat, decided once at creation.
    language: str | None = None


async def _validate_model_id(model_id: str | None) -> None:
    """Check that the model is in the catalog's **display list**.

    Why the display list rather than the registered list: a model with display turned
    off was deliberately taken down by an admin, so a new project must not pick it.

    Without this check an arbitrary string goes into the manifest, and the failure
    shows up on the first chat turn as AccessDenied (outside the IAM wildcard) or
    ValidationException (nonexistent profile) -- both visible only in backend logs.
    """
    if model_id is None:
        return
    allowed = {e.model_id for e in await app_module.model_catalog().displayed()}
    if model_id not in allowed:
        raise HTTPException(status_code=400,
                            detail=ec.MODEL_NOT_SELECTABLE)


#: Allowed output languages. Must be the same set as ProjectRegistry._LANGUAGES --
#: if a value accepted here hits the fallback there, the language the user chose is
#: silently ignored.
_LANGUAGES = ("ko", "en")


def _validate_language(language: str | None) -> None:
    """Only two values are allowed.

    If an arbitrary string goes into the manifest, place_rules cannot decide which
    instruction block to attach, and ProjectRegistry.get_language drops it to "ko" --
    that is, the language the user chose is silently ignored. Rejecting it at creation
    is the only place to eliminate that silence.
    """
    if language is None:
        return
    if language not in _LANGUAGES:
        raise HTTPException(status_code=400,
                            detail=ec.LANGUAGE_UNSUPPORTED)


class MembersBody(BaseModel):
    #: Email or username. What gets stored is the username (project_access.username_of).
    members: list[str]


def _access_view(pid: str, me: Principal) -> dict:
    """Values the screen uses to draw buttons according to permissions. members is given
    only to the owner and admins -- a viewing member has no need to know other viewers."""
    access = project_access.access_of(me, pid)
    view: dict = {"owner": app_module.registry.get_owner(pid), "access": access}
    if project_access.can_write(access):
        view["members"] = list(app_module.registry.get_members(pid))
    return view


@router.post("/projects")
async def create_project(body: CreateProject, me: Principal = Depends(require_user)):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    # Validate before creating the workspace -- creating and rolling back a local
    # directory and runner for a request that will be rejected is wasteful.
    await _validate_model_id(body.model_id)
    _validate_language(body.language)
    # effort comes from the catalog, not the request -- it is a value the admin set as a
    # pair with the model, and trusting the client's value would let a combination
    # outside the catalog into the manifest. It is **copied** together with the model
    # (the rationale is in write_manifest).
    effort = (await app_module.model_catalog().effort_of(body.model_id)
              if body.model_id else None)
    # Fixed here so the manifest and registry share the same created_at -- the list
    # sort key (creation date, ascending) stays the same across restarts.
    created_at = datetime.now(timezone.utc).isoformat()
    # **register must come before make_workspace.** The order is load-bearing.
    #
    # make_workspace calls driver_factory, and that factory assembles the driver by
    # **reading the registry** via project_language(pid) and project_model(pid).
    # make_workspace used to come first, so that read saw a not-yet-registered project
    # and picked up fallback values -- "ko" for language, the env default for the model.
    # And because the attached Workspace holds the driver built that way for the whole
    # process lifetime, **every turn** of a newly created English project ran in Korean
    # (observed 2026-08-04).
    #
    # This is why the symptom was confusing: the manifest, registry, and header badge
    # all correctly get "en". Only the driver is off, so it looks like the screen is in
    # English while only the conversation is in Korean. The model side is even quieter
    # -- with the env fallback it runs on the deployment's default model instead of the
    # chosen one, with no error and no log.
    #
    # Moving registration earlier adds one more thing to roll back on failure
    # (registry.remove in the except below). In return, the order matches the fact
    # that "the driver reads the registry" -- an alternative is to have the factory take
    # the values as arguments, but then driver_factory, make_workspace, and the restore
    # path would all need signature changes, and the lazy initialization path
    # (deps.ensure_workspace) would still read the registry. Leaving the readers as
    # they are and fixing the order is the narrower change.
    owner = project_access.normalize(me.username)
    app_module.registry.register(body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id,
                                 language=body.language, effort=effort, owner=owner)
    try:
        workspace = await app_module.make_workspace(body.project_id)
    except Exception:
        # Never leave a registration without a workspace -- that state becomes a
        # project that appears in the list but cannot be opened.
        app_module.registry.remove(body.project_id)
        raise
    if app_module.durable_projects_enabled():
        try:
            await write_manifest(app_module.projects_root_s3_factory(),
                                 body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id,
                                 language=body.language, effort=effort, owner=owner)
        except Exception:
            # Spec decision: do not silently create a project that would vanish on restart.
            _log.exception("manifest write failed for %s", body.project_id)
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("workspace cleanup after manifest failure failed")
            # Roll back the registration too (needed now that register comes first).
            app_module.registry.remove(body.project_id)
            raise HTTPException(status_code=500, detail="project persistence failed")
    app_module.registry.attach(body.project_id, workspace)
    return {"project_id": body.project_id, "name": body.name,
            "model_id": body.model_id,
            "effort": app_module.registry.get_effort(body.project_id),
            # Return the language that will actually be used (unset → "ko"). Returning
            # null would force the frontend to know the fallback rule as well.
            "language": app_module.registry.get_language(body.project_id),
            **_access_view(body.project_id, me)}

@router.get("/projects")
async def list_projects(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=50),
                        me: Principal = Depends(require_user)):
    # Paged list + S3 progress (fail-soft). Does not trigger workspace lazy
    # initialization -- durable project metadata (DynamoDB) is a later prod concern.
    # Filter to what the caller can see first, then slice the page -- the other way
    # round, total and page count would include other people's projects.
    ids = project_access.visible_ids(me)
    total = len(ids)
    page_ids = ids[(page - 1) * size : page * size]
    progresses = await asyncio.gather(*(_progress(pid) for pid in page_ids))
    return {
        "projects": [
            {"project_id": pid, "name": app_module.registry.get_name(pid),
             "created_at": app_module.registry.get_created_at(pid),
             "model_id": app_module.registry.get_model_id(pid),
             "effort": app_module.registry.get_effort(pid),
             "language": app_module.registry.get_language(pid),
             "progress": prog,
             **_access_view(pid, me)}
            for pid, prog in zip(page_ids, progresses)
        ],
        "total": total,
        "page": page,
        "size": size,
    }

@router.get("/projects/{pid}")
async def get_project(pid: str, me: Principal = Depends(require_user)):
    """Metadata for one project. This is what the header's model badge calls.

    Reads only the registry, without going through ensure_workspace -- a single badge
    must not trigger workspace lazy initialization (runner boot). _progress in
    list_projects reads S3 directly for the same reason.
    """
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    return {"project_id": pid,
            "name": app_module.registry.get_name(pid),
            "created_at": app_module.registry.get_created_at(pid),
            "model_id": app_module.registry.get_model_id(pid),
            "effort": app_module.registry.get_effort(pid),
            "language": app_module.registry.get_language(pid),
            **_access_view(pid, me)}


@router.put("/projects/{pid}/members")
async def set_members(pid: str, body: MembersBody, me: Principal = Depends(require_user)):
    """Replace the whole viewing-member list. Owner and admins only (it is a PUT, so the
    project_access gate blocks members with 403).

    Does not check whether the account exists: doing so would let anyone probe whether
    an account exists for any email through the difference in responses. A typo simply
    results in "visible to whoever signs in with that email", and the screen says so."""
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    try:
        members = tuple(project_access.username_of(m) for m in body.members)
    except ValueError:
        raise HTTPException(status_code=400, detail=ec.MEMBER_INVALID)
    if len(set(members)) > project_access.MAX_MEMBERS:
        raise HTTPException(status_code=400, detail=ec.MEMBER_INVALID)
    async with project_access.access_lock(pid):
        if not app_module.registry.is_registered(pid):
            raise HTTPException(status_code=404, detail="unknown project")
        try:
            await project_access.set_access(
                pid, app_module.registry.get_owner(pid) or project_access.SEED_ADMIN, members)
        except Exception:
            _log.exception("members update failed for %s", pid)
            raise HTTPException(status_code=500, detail="project persistence failed")
    return {"project_id": pid, **_access_view(pid, me)}

@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    """Delete everything (spec decision): runner stop (best-effort) → clean up the
    prototype's real footprint (500 on failure) → delete S3 sessions + artifacts (500 on
    failure, idempotent retry) → remove from the registry.

    Holds the same lock as access changes (members, ownership). Those read-modify-write
    the manifest, so if the prefix is deleted in between, that write resurrects
    project.json and the deleted project reappears in the list after a restart."""
    async with project_access.access_lock(pid):
        return await _delete_project(pid)


async def _delete_project(pid: str):
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    already_stopped = None
    if app_module.registry.has_workspace(pid):
        already_stopped = app_module.registry.get(pid)
        try:
            await already_stopped.stop()
        except Exception:
            _log.exception("runner stop failed for %s during delete (continuing)", pid)
    # A prototype's **real footprint** lives outside the S3 prefix: the local build
    # tree, the running preview process and its port, the access token (file +
    # in-memory cache), the build session, and the survey token index at the bucket
    # **root** (surveys/by-token/). delete_project_data below deletes only
    # projects/{pid}/ and sessions/session_{pid}/, so all of these used to remain --
    # in particular, while the token was alive, an already-shared preview link kept
    # opening even for a deleted project (the proxy does not check project
    # registration; it looks only at proto_host's token and state).
    #
    # This must run **before the S3 deletion**. The survey token index has no reverse
    # lookup, so it can only be reclaimed by reading the question files; if the prefix
    # is deleted first, that index stays forever, unreachable by any code (see
    # SurveyStore.purge). So a failure is cut off here with a 500: leaving the
    # registry and S3 intact keeps a retry meaningful (every step is idempotent).
    durable = app_module.durable_projects_enabled()
    failures = await purge_project_prototypes(
        pid,
        host=app_module.proto_host(),
        sessions=app_module.proto_sessions,
        s3=app_module.s3_store_factory(pid) if durable else None,
        survey_store_factory=app_module.survey_store_factory if durable else None,
        preview_store=prototype_store(pid),
    )
    if failures:
        _log.error("prototype cleanup failed for %s: %s", pid, ",".join(failures))
        raise HTTPException(status_code=500,
                            detail=f"prototype cleanup failed: {','.join(failures)}")
    # The workspace directory is deleted **by path** -- `runner.stop()` above also has
    # an rmtree, but it sits inside the `has_workspace(pid)` gate, and since startup
    # restore only calls `register()`, that flag is **False for every project not
    # opened since the restart** (the docstring of app.purge_local_workspace records
    # the observation). Also, stop's failures are swallowed, so if driver shutdown
    # fails the rmtree is skipped along with it.
    #
    # Placed before the S3 deletion so a failure is cut off with a 500: replying
    # "deleted" while documents remain locally would break the promise made to the user.
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
    # and attached during the S3 deletion await. The workspace returned by the final
    # remove is the cleanup point for that -- if it is the same object already stopped
    # above (the normal path with no race), skip the duplicate stop; if it is a
    # different object (a late-attached workspace), stop it so the VM does not leak.
    if removed is not None and removed is not already_stopped:
        try:
            await removed.stop()
        except Exception:
            _log.exception("runner stop failed for %s during final registry removal (continuing)", pid)
    return {"deleted": True}
