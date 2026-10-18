# backend/aipds/routes/models.py — model catalog.
#
# It is split into two routers because their permissions differ:
#   router       GET /models        — project creation screen combo box (users)
#   admin_router /admin/models*     — create, update, delete (admins)
#
# admin_router attaches require_admin to the whole router (the same discipline as
# admin_users.py) — this removes any chance of forgetting it on a route.
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response

from aipds import error_codes as ec
from aipds.auth.deps import require_admin
from aipds.model_catalog import CatalogError, Effort

_log = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Catalog policy violation → HTTP. Why readonly is 503: it is not a client error;
# it means the server is running without a bucket (an admin screen opened in local
# development).
_ERROR_STATUS = {
    "duplicate": 409,
    "too_many_displayed": 400,
    "not_found": 404,
    "readonly": 503,
    "stale": 409,
}

# Catalog errors for which the frontend dictionary has its own message. For the
# rest, the catalog's English sentence goes out as is (ModelTable shows the
# server's sentence as is).
_ERROR_DETAIL = {
    "stale": ec.MODEL_ORDER_STALE,
}


# PATCH/DELETE /admin/models/{model_id} takes {model_id} as a single URL path
# segment, not as :path (see admin_patch_model and admin_delete_model) — an id
# containing '/' can be registered via admin_add_model but can never be found
# again through that path. The character set must be restricted at registration
# time so that a single typo cannot permanently occupy a display slot
# (MAX_DISPLAYED=5) in a state the API cannot delete (one that requires
# hand-editing the catalog file). The spec (§5) character set is alphanumerics,
# '.', '-', and ':', but '_' is also allowed — although absent from the spec, it
# is a legitimate character that can appear in AWS model ids and does not break a
# segment, so wrongly rejecting real model ids by excluding it is a bigger risk
# than any risk from allowing it.
#
# `.` and `..` are rejected separately even though they consist only of allowed
# characters: RFC 3986 §5.2.4 dot-segment normalization happens on the **client**,
# so those two values can be registered, but a standard client can never construct
# and send that path. Measured (WHATWG URL parser — the same one the frontend's
# fetch uses):
#   new URL('/admin/models/.',  base).pathname === '/admin/models/'
#   new URL('/admin/models/..', base).pathname === '/admin/'
# In other words, exactly like '/', they become "registrable but undeletable"
# entries. `...` and `abc.` are not subject to normalization (it applies only to
# exactly the `.` and `..` segments), so they are allowed.
_MODEL_ID_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9.:_-]+$")


def _http_error(exc: CatalogError) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 500)
    if status >= 500:
        _log.warning("model catalog error (%s) -> %d", exc.code, status)
    # All of these messages are sentences we wrote, and they contain no
    # credentials or internal paths — the admin needs to know what to do, so we
    # show them as is.
    return HTTPException(status_code=status,
                         detail=_ERROR_DETAIL.get(exc.code, str(exc)))


class AddModel(BaseModel):
    name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    display: bool = True
    #: None = CLI default. Values outside the allowed set are 422 (Effort is the
    #: CLI's `--effort` value set).
    effort: Effort | None = None


class PatchModel(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    display: bool | None = None
    #: Omitted means unchanged; sending null clears it back to the CLI default —
    #: `model_fields_set` is checked to tell the two apart (admin_patch_model).
    effort: Effort | None = None


class ModelOrder(BaseModel):
    model_ids: list[str]


def _catalog():
    import aipds.app as app_module
    return app_module.model_catalog()


@router.get("/models")
async def list_displayed_models():
    """What the combo box calls. Only entries with display on, at most 5, with
    name, id, and effort.

    The display flag itself is not sent — it means nothing to regular users, and
    this removes the path where the frontend forgets to filter. effort is sent:
    even the same model varies greatly in speed depending on effort, so the person
    choosing needs to know it.
    """
    entries = await _catalog().displayed()
    return {"models": [{"name": e.name, "model_id": e.model_id, "effort": e.effort}
                       for e in entries]}


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
                                     display=body.display, effort=body.effort)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return entry.model_dump()


# Its method (PUT) differs from the `{model_id}` routes, so it does not collide
# even with a model named `order`.
@admin_router.put("/models/order")
async def admin_reorder_models(body: ModelOrder):
    try:
        entries = await _catalog().reorder(body.model_ids)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return {"models": [e.model_dump() for e in entries]}


@admin_router.patch("/models/{model_id}")
async def admin_patch_model(model_id: str, body: PatchModel):
    name = body.name.strip() if body.name is not None else None
    if name is not None and not name:
        raise HTTPException(status_code=422, detail=ec.NAME_REQUIRED)
    effort = ({"effort": body.effort} if "effort" in body.model_fields_set else {})
    try:
        entry = await _catalog().update(model_id, name=name, display=body.display,
                                        **effort)
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
