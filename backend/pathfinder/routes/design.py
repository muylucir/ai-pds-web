# backend/pathfinder/routes/design.py — 브랜드 디자인 프로필(관리자 전용).
#
# 라우터 전체에 require_admin을 붙인다(admin_users.py·models.py와 같은
# 규율) — 라우트마다 붙이는 것을 잊을 여지를 없앤다.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from starlette.responses import PlainTextResponse, Response

from pathfinder.auth.deps import require_admin
from pathfinder.auth.models import Principal
from pathfinder.design_profile import (MAX_DESIGN_BYTES, TEMPLATE_MD,
                                       DesignProfile, DesignProfileError)

_log = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# readonly가 503인 이유는 models.py와 같다: 클라이언트 잘못이 아니라 서버가
# 버킷 없이 떠 있다는 뜻이다(로컬 개발에서 관리자 화면을 연 경우).
_ERROR_STATUS = {"invalid": 400, "readonly": 503}


def _store():
    import pathfinder.app as app_module
    return app_module.design_profile_store()


def _http_error(exc: DesignProfileError) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 500)
    if status >= 500:
        _log.warning("design profile error (%s) -> %d", exc.code, status)
    # 우리가 쓴 문장이고 자격증명이나 내부 경로를 담지 않는다 — 관리자가 어느
    # 줄을 고쳐야 하는지 알아야 하므로 그대로 보여준다.
    return HTTPException(status_code=status, detail=str(exc))


def _view(profile: DesignProfile) -> dict:
    """화면용 축약. 원문(markdown)은 넣지 않는다 — /raw로 내려받는다."""
    return {"filename": profile.filename, "uploaded_at": profile.uploaded_at,
            "uploaded_by": profile.uploaded_by, "tokens": profile.tokens,
            "prose": profile.prose}


@admin_router.get("/design")
async def get_design_profile():
    profile = await _store().load()
    return {"profile": _view(profile) if profile is not None else None}


@admin_router.put("/design")
async def put_design_profile(file: UploadFile, request: Request,
                             me: Principal = Depends(require_admin)):
    # uploads.py와 같은 이중 방어: content-length는 클라이언트가 정하는 값이라
    # 보안 경계가 아니지만(아래 재검사가 권위 있다) 정직한 대용량이 디스크로
    # 스풀되는 것을 먼저 막는다. 64KB는 저장 용량이 아니라 컨텍스트 예산이다
    # — 산문은 매 빌드 워크스페이스와 에이전트 컨텍스트에 그대로 실리고,
    # 한국어는 같은 내용이 토큰을 1.66배 먹는다(design_profile.py 참고).
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_DESIGN_BYTES + 10_000:
        raise HTTPException(status_code=413,
                            detail=f"file exceeds {MAX_DESIGN_BYTES} bytes")
    filename = file.filename or "DESIGN.md"
    if not filename.lower().endswith(".md"):
        raise HTTPException(status_code=415, detail="only .md files are accepted")
    data = await file.read()
    if len(data) > MAX_DESIGN_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"file exceeds {MAX_DESIGN_BYTES} bytes")
    try:
        markdown = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="file must be UTF-8 text")
    try:
        profile = await _store().save(filename=filename,
                                      uploaded_by=me.username,
                                      markdown=markdown)
    except DesignProfileError as exc:
        raise _http_error(exc) from exc
    return {"profile": _view(profile)}


@admin_router.delete("/design", status_code=204)
async def delete_design_profile():
    try:
        await _store().remove()
    except DesignProfileError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)


@admin_router.get("/design/raw")
async def get_design_raw():
    profile = await _store().load()
    if profile is None:
        raise HTTPException(status_code=404, detail="no design profile")
    return PlainTextResponse(
        profile.markdown, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="DESIGN.md"'})


@admin_router.get("/design/template")
async def get_design_template():
    return PlainTextResponse(
        TEMPLATE_MD, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="DESIGN.md"'})
