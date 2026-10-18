# backend/aipds/routes/transfer.py — routes that move a project between instances.
#
# Export sends one project down as one zip (project_export.py), and import restores that zip on
# another instance (project_import.py). project_bundle.py owns the bundle format; this file owns
# only the HTTP contract.
#
# **The upload does not pass through this process.** Import has 3 steps —
# presign request → the browser PUTs directly to S3 → import request. A bundle has no upper bound
# on size, and requests that pass through the server hit nginx's `client_max_body_size` and the
# Next middleware's body limit (both set to match the attachment upload limit) — this is the
# only way to receive a large bundle.
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from aipds import error_codes as ec
from aipds.import_staging import (
    CONTENT_TYPE,
    MAX_IMPORT_BYTES,
    PRESIGN_EXPIRES_IN,
    new_upload_id,
)
from aipds.model_catalog import EFFORTS
from aipds.pathsafe import reject_unsafe_segment
from aipds.project_bundle import MANIFEST_NAME, BundleError, parse_manifest
from aipds.project_export import write_bundle
from aipds.project_import import (
    WARN_MODEL_UNAVAILABLE,
    apply_bundle,
)
from aipds.auth.deps import require_user
from aipds.auth.models import Principal
from aipds.project_access import normalize, require_project_owner
from aipds.project_store import write_manifest

_log = logging.getLogger(__name__)


def _reject_traversal_params(request: Request) -> None:
    """`{pid}` must be a single plain path segment.

    Same reason as the guard of the same name in routes/prototypes.py: this value goes into a
    local directory name (`{proto_root}/{pid}/…`) and an S3 key prefix, and a percent-encoded
    `%2e%2e` arrives in `path_params` already decoded and is routed normally. Answering with 404
    is also the same as there — it means the address points to nothing.
    """
    value = request.path_params.get("pid")
    if value is None:
        return
    try:
        reject_unsafe_segment(value)
    except ValueError:
        _log.warning("rejected unsafe pid in transfer route: %r", value)
        raise HTTPException(status_code=404, detail="invalid pid")


router = APIRouter(dependencies=[Depends(_reject_traversal_params)])


def _content_disposition(pid: str, exported_at: str) -> str:
    """RFC 6266/5987. `pid` can be unvalidated user input (including Korean), so raw
    interpolation becomes a UnicodeEncodeError (500) under latin-1 header encoding —
    routes/artifacts.py has the same shape for the same reason."""
    stamp = re.sub(r"[^0-9]", "", exported_at)[:14]
    name = f"aipds-export-{pid}-{stamp}.zip"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "aipds-export.zip"
    return (f'attachment; filename="{safe}"; '
            f"filename*=UTF-8''{quote(name, safe='')}")


@router.get("/projects/{pid}/export", dependencies=[Depends(require_project_owner)])
async def export_project(pid: str):
    """The whole project as a bundle zip. Owners and admins only — it is a GET but not a read
    (project_access.require_project_owner). The bundle does not carry owners or members.

    **If there is a live build session, it is a 409.** Packing while the agent is writing to
    that tree would include half-written source, and that zip is not an honest snapshot.
    Discovery turns are not blocked — under the file-contract model, the S3 state at that
    moment is a valid snapshot, and making users wait for the turn to end costs more in a
    workshop.
    """
    import aipds.app as app_module
    from aipds.proto import layout as proto_layout
    from aipds.routes.prototypes import _live_session

    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")

    s3 = app_module.s3_store_factory(pid)
    for slug in proto_layout.discover(await s3.list(proto_layout.DISCOVERY_PREFIX)):
        if _live_session(pid, slug) is not None:
            raise HTTPException(status_code=409,
                                detail=ec.BUILD_SESSION_ACTIVE)

    exported_at = datetime.now(timezone.utc).isoformat()
    project = {
        "name": app_module.registry.get_name(pid),
        "created_at": app_module.registry.get_created_at(pid),
        "model_id": app_module.registry.get_model_id(pid),
        "effort": app_module.registry.get_effort(pid),
        "language": app_module.registry.get_language(pid),
    }
    # delete=False + BackgroundTask: deleted **after** FileResponse has streamed the whole body.
    # Wrapping it in `with` would make the file disappear before the response is even built.
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    dest = Path(tmp.name)
    try:
        await write_bundle(dest, project_id=pid, s3=s3,
                           proto_root=app_module._proto_root(),
                           project=project, exported_at=exported_at)
    except BaseException:
        _cleanup(dest)
        raise
    return FileResponse(
        dest, media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(pid, exported_at)},
        background=BackgroundTask(_cleanup, dest))


def _cleanup(path: Path) -> None:
    """Delete the temporary bundle. Failures are only logged — the response has already gone
    out."""
    try:
        os.unlink(path)
    except OSError:
        _log.warning("could not remove temporary bundle %s", path, exc_info=True)


# ---- Import ----
#
# Three steps: presign request → the browser PUTs directly to S3 → import request.
#
# **Why the paths are not under `/projects/…`.** `POST /projects/import/uploads` has the same
# shape as `POST /projects/{pid}/uploads` (reference material upload), and which one wins is
# decided by router registration order. That kind of consistency breaks silently — a user who
# creates a project named `import` would see their project's uploads go to import staging. With
# a separate noun, that trap does not exist.


class ImportUploadRequest(BaseModel):
    #: The bundle size the client announced. **Not an authoritative value** — it is a
    #: convenience so that an upload over the limit never starts; the real check is
    #: `head_object` at import time.
    size_bytes: int


class ImportRequest(BaseModel):
    upload_id: str
    #: If unspecified, the original id the bundle states is used. If that id already exists it is
    #: a 409, and the screen then fills this field and calls again **with the same upload_id**.
    project_id: str | None = None


#: The shape of the staging id we issue (uuid4 hex from `import_staging.new_upload_id`).
#: This value becomes the S3 key, so enforcing its shape fully closes traversal —
#: no room is left for the client to choose the key.
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _staging():
    """Staging. 503 if there is no bucket — degrading silently would let the user find out only
    after uploading hundreds of MB."""
    import aipds.app as app_module
    try:
        return app_module.import_staging()
    except RuntimeError:
        _log.exception("project import is unavailable")
        raise HTTPException(status_code=503, detail=ec.IMPORT_UNAVAILABLE)


@router.post("/project-imports/uploads", status_code=201)
async def create_import_upload(body: ImportUploadRequest):
    """Presigned PUT URL to upload the bundle to. The upload does not pass through this
    process."""
    if body.size_bytes <= 0 or body.size_bytes > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail=ec.EXPORT_TOO_LARGE)
    staging = _staging()
    upload_id = new_upload_id()
    url = await staging.presign_put(upload_id)
    return {"upload_id": upload_id, "url": url,
            "content_type": CONTENT_TYPE,
            "expires_in": PRESIGN_EXPIRES_IN,
            "max_bytes": MAX_IMPORT_BYTES}


@router.post("/project-imports", status_code=201)
async def import_project(body: ImportRequest, me: Principal = Depends(require_user)):
    """Restore a staged bundle into a project.

    **The order is load-bearing.** Contents → `project.json` → registration. `project.json` is the
    source of truth for "this project exists" (`restore_projects` scans for it), so writing it
    first would let a mid-way failure leave a project that **shows in the list but is empty
    inside**, and that state survives restarts.

    **A 409 does not delete the staging.** An id collision is only known by reading the manifest,
    and the manifest can only be read after the upload finishes — deleting it here would force the
    user to re-upload the whole bundle just to change one id.

    The importer becomes the owner. The bundle does not carry the owner — a username on another
    instance may be a different person on this instance.
    """
    import aipds.app as app_module
    owner = normalize(me.username)

    if not _UPLOAD_ID_RE.match(body.upload_id):
        raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)
    staging = _staging()
    upload_id = body.upload_id

    size = await staging.size(upload_id)
    if size is None:
        raise HTTPException(status_code=400, detail=ec.UPLOAD_MISSING)
    if size > MAX_IMPORT_BYTES:
        # Reject **before** downloading — pulling an over-limit object to disk and then rejecting
        # it is not a defense.
        await staging.delete(upload_id)
        raise HTTPException(status_code=413, detail=ec.EXPORT_TOO_LARGE)

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    bundle = Path(tmp.name)
    try:
        await staging.download_to(upload_id, bundle)
        try:
            manifest = await asyncio.to_thread(_read_manifest, bundle)
        except BundleError as e:
            await staging.delete(upload_id)
            _log.warning("rejected bundle %s: %s", upload_id, e)
            raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)

        pid = body.project_id or manifest["source_project_id"]
        try:
            reject_unsafe_segment(pid)
        except ValueError:
            raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)
        if app_module.registry.is_registered(pid):
            raise HTTPException(status_code=409, detail=ec.PROJECT_EXISTS)

        project = manifest["project"]
        model_id, warnings = await _resolve_model(project.get("model_id"))
        # effort is brought over as is from the original project (it is a value copied as a
        # pair with the model). If the model was cleared because it is unusable, effort is
        # cleared too — attaching it to another model would make a combination nobody chose.
        # Bundles from before this field have no key and get None (the CLI default), which is
        # how the original actually ran.
        effort = project.get("effort") if model_id is not None else None
        if effort not in EFFORTS:
            effort = None
        language = project.get("language")
        if language not in ("ko", "en"):
            language = None  # the registry settles it as "ko"

        try:
            data = await apply_bundle(
                bundle, project_id=pid,
                s3=app_module.s3_store_factory(pid),
                surveys_root=app_module.surveys_root_s3_factory(),
                proto_root=app_module._proto_root())
        except BundleError as e:
            # The fact that a bundle is not a shape we know does not surface only when reading
            # `export.json` — entry names attempting an escape, compression bombs, and unreadable
            # survey definitions all come out here. All of them are the same 400:
            # what the user should do is pick a different file, and re-uploading would not help,
            # so the staging is deleted too. apply_bundle has already rolled back what it
            # wrote.
            await staging.delete(upload_id)
            _log.warning("rejected bundle %s during import: %s", upload_id, e)
            raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)
        try:
            await write_manifest(app_module.projects_root_s3_factory(), pid,
                                 project.get("name"),
                                 created_at=project.get("created_at"),
                                 model_id=model_id, language=language, effort=effort,
                                 owner=owner)
        except Exception:
            # Without a manifest, the project disappears after a restart. The create route
            # makes the same judgment for the same reason (routes/projects.py).
            _log.exception("manifest write failed for imported project %s", pid)
            await data.discard()
            raise HTTPException(status_code=500, detail=ec.IMPORT_FAILED)
        app_module.registry.register(pid, project.get("name"),
                                    created_at=project.get("created_at"),
                                    model_id=model_id, language=language, effort=effort,
                                    owner=owner)
    finally:
        _cleanup(bundle)

    await staging.delete(upload_id)
    return {"project_id": pid,
            "name": project.get("name"),
            "language": app_module.registry.get_language(pid),
            "model_id": model_id,
            "effort": app_module.registry.get_effort(pid),
            "source_project_id": manifest["source_project_id"],
            "counts": data.counts,
            "warnings": warnings + data.warnings}


def _read_manifest(bundle: Path) -> dict:
    """Read and validate only `export.json` from the bundle. A zip that will not even open is also
    "not a shape we know", so it is collected into the same exception."""
    try:
        with zipfile.ZipFile(bundle) as zf:
            return parse_manifest(zf.read(MANIFEST_NAME))
    except KeyError as e:
        raise BundleError(f"bundle has no {MANIFEST_NAME}") from e
    except zipfile.BadZipFile as e:
        raise BundleError("upload is not a zip archive") from e


async def _resolve_model(model_id: str | None) -> tuple[str | None, list[dict]]:
    """Check whether the original's model can be used on this instance.

    **It must not be passed through silently.** If a model id not in the catalog goes into the
    manifest, the failure shows up as `AccessDenied` (outside the IAM wildcard) or
    `ValidationException` on the first conversation turn, and both are left only in the backend
    logs. Degrading to the env default keeps the project running, but **on a model nobody chose**,
    so that fact is raised as a warning — it is the quietest failure in this feature.
    """
    import aipds.app as app_module

    if model_id is None:
        return None, []
    try:
        allowed = {e.model_id for e in await app_module.model_catalog().displayed()}
    except Exception:
        # Failing to read the catalog does not block the import — the model has a fallback, and
        # raising a 500 here would fail at a step where there is nothing to roll back.
        _log.warning("model catalog unavailable; importing without a model",
                     exc_info=True)
        return None, [{"code": WARN_MODEL_UNAVAILABLE, "count": 1}]
    if model_id in allowed:
        return model_id, []
    _log.info("imported project's model %r is not selectable here", model_id)
    return None, [{"code": WARN_MODEL_UNAVAILABLE, "count": 1}]
