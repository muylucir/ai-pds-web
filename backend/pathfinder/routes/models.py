# backend/pathfinder/routes/models.py — 모델 카탈로그.
#
# 두 라우터로 나뉘는 이유는 권한이 다르기 때문이다:
#   router       GET /models        — 프로젝트 생성 화면의 콤보박스(일반 사용자)
#   admin_router /admin/models*     — 등록·수정·삭제(관리자)
#
# admin_router는 라우터 전체에 require_admin을 붙인다(admin_users.py와 같은
# 규율) — 라우트마다 붙이는 것을 잊을 여지를 없앤다.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response

from pathfinder.auth.deps import require_admin
from pathfinder.model_catalog import CatalogError

_log = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# 카탈로그 정책 위반 → HTTP. readonly가 503인 이유: 클라이언트 잘못이 아니라
# 서버가 버킷 없이 떠 있다는 뜻이다(로컬 개발에서 관리자 화면을 연 경우).
_ERROR_STATUS = {
    "duplicate": 409,
    "too_many_displayed": 400,
    "not_found": 404,
    "readonly": 503,
}


def _http_error(exc: CatalogError) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 500)
    if status >= 500:
        _log.warning("model catalog error (%s) -> %d", exc.code, status)
    # 이 메시지들은 전부 우리가 쓴 문장이고 자격증명이나 내부 경로를 담지
    # 않는다 — 관리자가 무엇을 해야 하는지 알아야 하므로 그대로 보여준다.
    return HTTPException(status_code=status, detail=str(exc))


class AddModel(BaseModel):
    name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    display: bool = True


class PatchModel(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    display: bool | None = None


def _catalog():
    import pathfinder.app as app_module
    return app_module.model_catalog()


@router.get("/models")
async def list_displayed_models():
    """콤보박스가 부르는 곳. display가 켜진 것만, 최대 5개, 이름과 id만.

    display 플래그 자체는 보내지 않는다 — 일반 사용자에게 의미가 없고,
    프론트가 필터링을 잊는 경로를 없앤다.
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
        raise HTTPException(status_code=422, detail="이름을 입력하세요.")
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
        raise HTTPException(status_code=422, detail="이름을 입력하세요.")
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
