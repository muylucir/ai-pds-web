# backend/aipds/routes/models.py -- the model catalog.
#
# It splits into two routers because the permissions differ:
#   router       GET /models        -- the combo box on the create-project screen
#                                     (ordinary users)
#   admin_router /admin/models*     -- add, edit, delete (admins)
#
# admin_router applies require_admin to the whole router (the same discipline as
# admin_users.py), removing any chance of forgetting it on an individual route.
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response

from aipds import error_codes as ec
from aipds.auth.deps import require_admin
from aipds.model_catalog import CatalogError

_log = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Catalog policy violations -> HTTP. readonly is a 503 because it is not the
# client's fault: it means the server is running without a bucket (which happens
# when the admin screen is opened in local development).
_ERROR_STATUS = {
    "duplicate": 409,
    "too_many_displayed": 400,
    "not_found": 404,
    "readonly": 503,
}


# PATCH/DELETE /admin/models/{model_id} takes {model_id} as a single URL path
# segment rather than a :path (see admin_patch_model and admin_delete_model) -- an
# id containing '/' can be registered through admin_add_model but can never be
# found again through that route. Restricting the character set at registration
# time is what stops one typo from permanently occupying a display slot
# (MAX_DISPLAYED=5) in a state that cannot be deleted through the API (one that
# requires editing the catalog file by hand). The spec (§5) allows alphanumerics,
# '.', '-' and ':'; '_' is allowed on top of that -- it is legal, does not break a
# segment, and can appear in AWS model ids, so wrongly rejecting a real model id by
# excluding it is a bigger risk than allowing it.
#
# `.` and `..` are rejected separately even though they consist only of permitted
# characters: RFC 3986 §5.2.4 dot-segment normalisation happens **on the client**,
# so those two values can be registered but a standards-compliant client cannot
# construct that path to reach them. Measured (WHATWG URL parser -- the same one the
# frontend's fetch uses):
#   new URL('/admin/models/.',  base).pathname === '/admin/models/'
#   new URL('/admin/models/..', base).pathname === '/admin/'
# In other words they become the same "registerable but undeletable" entry as '/'.
# `...` and `abc.` are not subject to normalisation (it applies to exactly the `.`
# and `..` segments), so they are allowed.
_MODEL_ID_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9.:_-]+$")


def _http_error(exc: CatalogError) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 500)
    if status >= 500:
        _log.warning("model catalog error (%s) -> %d", exc.code, status)
    # These messages are all sentences we wrote and carry no credentials or
    # internal paths -- the admin needs to know what to do, so they are shown
    # verbatim.
    return HTTPException(status_code=status, detail=str(exc))


class AddModel(BaseModel):
    name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    display: bool = True


class PatchModel(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    display: bool | None = None


def _catalog():
    import aipds.app as app_module
    return app_module.model_catalog()


@router.get("/models")
async def list_displayed_models():
    """What the combo box calls. Only the display-enabled entries, at most 5, name and
    id only.

    The display flag itself is not sent: it means nothing to an ordinary user, and
    leaving it out removes any path where the frontend forgets to filter.
    """
    entries = await _catalog().displayed()
    return {"models": [{"name": e.name, "model_id": e.model_id} for e in entries]}


@admin_router.get("/models")
async def admin_list_models():
    entries = await _catalog().load()
    return {"models": [e.model_dump() for e in entries]}


@admin_router.post("/models", status_code=201)
async def admin_add_model(body: AddModel):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail=ec.NAME_REQUIRED)
    if not body.model_id.strip():
        raise HTTPException(status_code=422, detail=ec.MODEL_ID_REQUIRED)
    if not _MODEL_ID_RE.match(body.model_id.strip()):
        raise HTTPException(status_code=422, detail=ec.MODEL_ID_CHARSET)
    try:
        entry = await _catalog().add(body.name.strip(), body.model_id.strip(),
                                     display=body.display)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return entry.model_dump()


@admin_router.patch("/models/{model_id}")
async def admin_patch_model(model_id: str, body: PatchModel):
    name = body.name.strip() if body.name is not None else None
    if name is not None and not name:
        raise HTTPException(status_code=422, detail=ec.NAME_REQUIRED)
    try:
        entry = await _catalog().update(model_id, name=name, display=body.display)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return entry.model_dump()


@admin_router.delete("/models/{model_id}", status_code=204)
async def admin_delete_model(model_id: str):
    try:
        await _catalog().remove(model_id)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)
