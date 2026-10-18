# backend/aipds/routes/design.py — brand design profile (admin only).
#
# require_admin is attached to the whole router (the same discipline as
# admin_users.py and models.py) — this removes any chance of forgetting it on a
# route.
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

# readonly is 503 for the same reason as in models.py: it is not a client error;
# it means the server is running without a bucket (an admin screen opened in local
# development).
_ERROR_STATUS = {"invalid": 400, "readonly": 503}


def _store():
    import aipds.app as app_module
    return app_module.design_profile_store()


def _http_error(exc: DesignProfileError) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 500)
    if status >= 500:
        _log.warning("design profile error (%s) -> %d", exc.code, status)
    # These are sentences we wrote, and they contain no credentials or internal
    # paths — the admin needs to know which line to fix, so we show them as is.
    return HTTPException(status_code=status, detail=str(exc))


def _view(profile: DesignProfile) -> dict:
    """Condensed form for the screen. The original (markdown) is not included — it
    is downloaded via /raw."""
    return {"filename": profile.filename, "uploaded_at": profile.uploaded_at,
            "uploaded_by": profile.uploaded_by, "tokens": profile.tokens,
            "prose": profile.prose, "warnings": _warnings(profile)}


def _warnings(profile: DesignProfile) -> list[str]:
    """**Derived** from the stored object — it is not stored, for the same reason
    as tokens/prose (design_profile.py: storing derived values alongside makes the
    stored object go stale). Because it is derived, reopening via GET produces the
    same sentence as the upload response.

    Without tokens, the brand never reaches the screen — only the prose is passed
    to the agent, and whether the agent reflects it is not enforced (measured
    2026-08-19: with the same zero-token profile, one project reflected it and
    another did not). The screen has to say so.
    """
    return [] if profile.tokens else ["no-tokens"]


async def _read_markdown(file: UploadFile, request: Request) -> tuple[str, str]:
    """(filename, markdown). Makes preview and PUT pass through the **same** gate
    — if they diverged, a file that passed preview would be rejected on save.

    The same double defense as uploads.py: content-length is a value the client
    sets, so it is not a security boundary (the recheck below is authoritative),
    but it stops an honest large upload from being spooled to disk first. 64KB is
    not a storage limit but a **context budget** — the prose is carried as is into
    every build workspace and the agent context, and Korean consumes 1.66x the
    tokens for the same content (see design_profile.py).
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
    """Tokens confirmed on the screen. Values are not validated here — after
    injection, `parse_design_md` rejects them with the same sentence (including
    line numbers) as for a hand-written file. Validating again here would mean two
    copies of the parser."""
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
    """Does not save; only answers "which tokens come out of this document".

    The ```tokens fence is a convention of our format only, so a DESIGN.md
    produced elsewhere arrives without a fence (measured 2026-08-19: that is why
    the brand never reached the screen). Extracting values from such a document
    involves **points where a person has to decide** — if the document gives
    different colors to the brand heading and the CTA, the document does not
    answer which one is `primary`. So before saving, this route returns a proposal
    and the screen gets it confirmed.
    """
    import aipds.app as app_module
    _, markdown = await _read_markdown(file, request)
    from_fence = has_fence(markdown)
    try:
        tokens, warnings = await extract_tokens(
            markdown, None if from_fence else app_module.design_token_extractor())
    except DesignProfileError as exc:
        # A fence exists but its contents are wrong — point it out with the same
        # sentence as for a hand-written file.
        raise _http_error(exc) from exc
    origin = "fence" if from_fence else ("extracted" if tokens else "none")
    return {"tokens": tokens, "origin": origin, "warnings": warnings}


@admin_router.put("/design")
async def put_design_profile(file: UploadFile, request: Request,
                             tokens: str | None = Form(default=None),
                             me: Principal = Depends(require_admin)):
    filename, markdown = await _read_markdown(file, request)
    # Confirmed tokens are saved **embedded in the original as a fence**. This
    # keeps the stored object as a single "original markdown + metadata" (derived
    # values are not stored separately), so a file downloaded via /raw is
    # indistinguishable from a hand-written one on the next upload.
    #
    # If the file already has a fence, this field is **ignored** — the fence is
    # authoritative. Otherwise the values sent by the screen could overwrite
    # values the admin wrote by hand.
    if tokens is not None and not has_fence(markdown):
        confirmed = _confirmed_tokens(tokens)
        if confirmed:
            markdown = inject_fence(markdown, confirmed)
            # Why re-measure after injection: so we never end up rejecting, on
            # re-upload, a file that we ourselves saved.
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
