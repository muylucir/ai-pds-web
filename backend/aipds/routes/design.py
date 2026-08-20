# backend/pathfinder/routes/design.py — 브랜드 디자인 프로필(관리자 전용).
#
# 라우터 전체에 require_admin을 붙인다(admin_users.py·models.py와 같은
# 규율) — 라우트마다 붙이는 것을 잊을 여지를 없앤다.
from __future__ import annotations

import logging

import json

from fastapi import (APIRouter, Depends, Form, HTTPException, Request,
                     UploadFile)
from starlette.responses import PlainTextResponse, Response

from aipds.auth.deps import require_admin
from aipds.auth.models import Principal
from aipds.design_profile import (MAX_DESIGN_BYTES, TEMPLATE_MD,
                                       DesignProfile, DesignProfileError)
from aipds.design_tokens import extract_tokens, has_fence, inject_fence

_log = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# readonly가 503인 이유는 models.py와 같다: 클라이언트 잘못이 아니라 서버가
# 버킷 없이 떠 있다는 뜻이다(로컬 개발에서 관리자 화면을 연 경우).
_ERROR_STATUS = {"invalid": 400, "readonly": 503}


def _store():
    import aipds.app as app_module
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
            "prose": profile.prose, "warnings": _warnings(profile)}


def _warnings(profile: DesignProfile) -> list[str]:
    """저장물에서 **유도한다** — 저장하지 않는 이유는 tokens/prose와 같다
    (design_profile.py: 파생값을 함께 저장하면 저장물이 낡는다). 유도하는 덕분에
    GET으로 다시 열어도 업로드 응답과 같은 문장이 나온다.

    토큰이 없으면 브랜드는 화면에 닿지 않는다 — 산문만 에이전트에게 전달되고,
    그것을 반영하는지는 강제되지 않는다(2026-08-19 실측: 같은 0토큰 프로필에서
    한 프로젝트는 반영됐고 다른 하나는 안 됐다). 그 사실을 화면이 말해야 한다.
    """
    return [] if profile.tokens else ["no-tokens"]


async def _read_markdown(file: UploadFile, request: Request) -> tuple[str, str]:
    """(filename, markdown). preview와 PUT이 **같은** 관문을 지나게 한다 —
    갈리면 preview를 통과한 파일이 저장에서 거부된다.

    uploads.py와 같은 이중 방어: content-length는 클라이언트가 정하는 값이라 보안
    경계가 아니지만(아래 재검사가 권위 있다) 정직한 대용량이 디스크로 스풀되는
    것을 먼저 막는다. 64KB는 저장 용량이 아니라 **컨텍스트 예산**이다 — 산문은 매
    빌드 워크스페이스와 에이전트 컨텍스트에 그대로 실리고, 한국어는 같은 내용이
    토큰을 1.66배 먹는다(design_profile.py 참고).
    """
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
        return filename, data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="file must be UTF-8 text")


def _confirmed_tokens(field: str) -> dict[str, str]:
    """화면이 확인한 토큰. 값 검증은 하지 않는다 — 주입한 뒤 `parse_design_md`가
    사람이 쓴 경우와 같은 문장(줄 번호 포함)으로 거부한다. 여기서 다시 검증하면
    파서가 두 벌이 된다."""
    try:
        parsed = json.loads(field)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="tokens must be a JSON object")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400,
                            detail="tokens must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items()}


@admin_router.get("/design")
async def get_design_profile():
    profile = await _store().load()
    return {"profile": _view(profile) if profile is not None else None}


@admin_router.post("/design/preview")
async def preview_design_profile(file: UploadFile, request: Request):
    """저장하지 않고 "이 문서에서 어떤 토큰이 나오는가"만 답한다.

    ```tokens 펜스는 우리 서식에만 있는 관례라, 밖에서 만들어진 DESIGN.md는
    펜스 없이 올라온다(2026-08-19 실측: 그래서 브랜드가 화면에 닿지 않았다).
    그 문서에서 값을 뽑는 판단에는 **사람이 끊어야 하는 자리**가 있다 — 문서가
    브랜드 헤딩과 CTA에 서로 다른 색을 주면 어느 것이 `primary`인지는 문서가
    답하지 않는다. 그래서 저장 전에 이 라우트가 제안을 돌려주고 화면이 확인받는다.
    """
    import aipds.app as app_module
    _, markdown = await _read_markdown(file, request)
    from_fence = has_fence(markdown)
    try:
        tokens, warnings = await extract_tokens(
            markdown, None if from_fence else app_module.design_token_extractor())
    except DesignProfileError as exc:
        # 펜스가 있는데 그 안이 틀린 경우다 — 사람이 쓴 파일과 같은 문장으로 짚어준다.
        raise _http_error(exc) from exc
    origin = "fence" if from_fence else ("extracted" if tokens else "none")
    return {"tokens": tokens, "origin": origin, "warnings": warnings}


@admin_router.put("/design")
async def put_design_profile(file: UploadFile, request: Request,
                             tokens: str | None = Form(default=None),
                             me: Principal = Depends(require_admin)):
    filename, markdown = await _read_markdown(file, request)
    # 확인된 토큰은 **원문에 펜스로 심어** 저장한다. 저장물을 "원문 markdown +
    # 메타" 하나로 유지하는 방법이고(파생값을 따로 저장하지 않는다), 그래서
    # /raw로 내려받은 파일이 다음번 업로드에서 손으로 쓴 것과 구분되지 않는다.
    #
    # 파일에 이미 펜스가 있으면 이 필드를 **무시한다** — 펜스가 권위다. 그렇지
    # 않으면 화면이 보낸 값이 관리자가 손으로 쓴 값을 덮을 수 있다.
    if tokens is not None and not has_fence(markdown):
        confirmed = _confirmed_tokens(tokens)
        if confirmed:
            markdown = inject_fence(markdown, confirmed)
            # 주입 후 다시 재는 이유: 우리가 저장한 파일을 우리가 재업로드에서
            # 거부하는 상태를 만들지 않는다.
            if len(markdown.encode("utf-8")) > MAX_DESIGN_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"file with the tokens block exceeds "
                           f"{MAX_DESIGN_BYTES} bytes")
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
