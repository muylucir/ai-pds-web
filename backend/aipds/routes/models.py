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

# 카탈로그 정책 위반 → HTTP. readonly가 503인 이유: 클라이언트 잘못이 아니라
# 서버가 버킷 없이 떠 있다는 뜻이다(로컬 개발에서 관리자 화면을 연 경우).
_ERROR_STATUS = {
    "duplicate": 409,
    "too_many_displayed": 400,
    "not_found": 404,
    "readonly": 503,
}


# PATCH/DELETE /admin/models/{model_id}는 {model_id}를 :path가 아닌 단일 URL
# 경로 세그먼트로 받는다(admin_patch_model·admin_delete_model 참고) — '/'가
# 들어간 id는 admin_add_model로 등록은 되어도 그 경로로는 다시 찾을 수 없다.
# 등록 시점에 문자셋을 막아야 오탈자 하나가 표시 슬롯(MAX_DISPLAYED=5)을
# 영구히 점유하고 API로 지울 수 없는 상태(카탈로그 파일을 손으로 고쳐야
# 하는 상태)를 만들지 않는다. 스펙(§5)의 문자셋은 영숫자·.·-·:이지만 '_'를
# 추가로 허용한다 — 스펙엔 없어도 AWS 모델 id에 나타날 수 있는 합법적이고
# 세그먼트를 깨지 않는 문자라서, 이걸 빼서 실제 모델 id를 잘못 거부하는
# 쪽이 이걸 허용해서 생기는 위험보다 크다.
#
# `.`와 `..`는 허용 문자로만 되어 있어도 따로 거부한다: RFC 3986 §5.2.4의
# dot-segment 정규화는 **클라이언트**에서 일어나므로, 그 두 값은 등록은 되지만
# 표준 클라이언트가 그 경로를 만들어 보낼 수 없다. 실측(WHATWG URL 파서 —
# 프론트의 fetch가 쓰는 것과 같은 것):
#   new URL('/admin/models/.',  base).pathname === '/admin/models/'
#   new URL('/admin/models/..', base).pathname === '/admin/'
# 즉 '/'와 똑같이 "등록은 되는데 지울 수 없는" 항목이 된다. `...`나 `abc.`는
# 정규화 대상이 아니므로(정확히 `.`과 `..` 세그먼트에만 적용된다) 허용한다.
_MODEL_ID_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9.:_-]+$")


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
    import aipds.app as app_module
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
