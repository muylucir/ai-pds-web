# backend/aipds/routes/design.py -- the brand design profile (admin only).
#
# require_admin is applied to the whole router (the same discipline as
# admin_users.py and models.py), removing any chance of forgetting it on an
# individual route.
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

# readonly is a 503 for the same reason as in models.py: it is not the client's
# fault but means the server is running without a bucket (which happens when the
# admin screen is opened in local development).
_ERROR_STATUS = {"invalid": 400, "readonly": 503}


def _store():
    import aipds.app as app_module
    return app_module.design_profile_store()


def _http_error(exc: DesignProfileError) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 500)
    if status >= 500:
        _log.warning("design profile error (%s) -> %d", exc.code, status)
    # Sentences we wrote, carrying no credentials or internal paths -- the admin
    # needs to know which line to fix, so they are shown verbatim.
    return HTTPException(status_code=status, detail=str(exc))


def _view(profile: DesignProfile) -> dict:
    """The abbreviated form for the screen. The original markdown is not included --
    it is downloaded through /raw."""
    return {"filename": profile.filename, "uploaded_at": profile.uploaded_at,
            "uploaded_by": profile.uploaded_by, "tokens": profile.tokens,
            "prose": profile.prose, "warnings": _warnings(profile)}


def _warnings(profile: DesignProfile) -> list[str]:
    """**Derived** from the stored object -- not stored, for the same reason as
    tokens/prose (design_profile.py: storing derived values alongside makes the stored
    object go stale). Deriving them is why reopening through GET produces the same
    sentences as the upload response did.

    With no tokens the brand never reaches the screen: only the prose is passed to the
    agent, and whether it honours that is not enforced (measured 2026-08-19: from the
    same zero-token profile one project reflected it and another did not). The screen
    has to say so.
    """
    return [] if profile.tokens else ["no-tokens"]


async def _read_markdown(file: UploadFile, request: Request) -> tuple[str, str]:
    """(filename, markdown). Makes preview and PUT pass through **the same** gate: if
    they diverge, a file that passed preview gets rejected on save.

    The same double defence as uploads.py: content-length is a value the client sets
    and so is not a security boundary (the re-check below is authoritative), but it
    stops an honestly large upload from spooling to disk first. The 64KB limit is not
    about storage but about the **context budget** -- the prose is carried verbatim
    into every build workspace and agent context, and the same content costs 1.66x the
    tokens in Korean (see design_profile.py).
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
    """The tokens the screen confirmed. The values are not validated here: after
    injection, `parse_design_md` rejects them with the same sentence (line number
    included) a hand-written file would get. Validating again here would make two
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
    """Answer only "which tokens does this document yield", without storing anything.

    The ```tokens fence is a convention that exists only in our own format, so a
    DESIGN.md produced elsewhere arrives without one (measured 2026-08-19: that is why
    the brand never reached the screen). Extracting values from such a document
    involves **a decision a human has to make**: if the document gives different
    colours to the brand heading and to the CTA, the document does not answer which of
    them is `primary`. So before storing, this route returns proposals and the screen
    gets them confirmed.
    """
    import aipds.app as app_module
    _, markdown = await _read_markdown(file, request)
    from_fence = has_fence(markdown)
    try:
        tokens, warnings = await extract_tokens(
            markdown, None if from_fence else app_module.design_token_extractor())
    except DesignProfileError as exc:
        # The fence is present but wrong inside -- point at it with the same
        # sentence a hand-written file would get.
        raise _http_error(exc) from exc
    origin = "fence" if from_fence else ("extracted" if tokens else "none")
    return {"tokens": tokens, "origin": origin, "warnings": warnings}


@admin_router.put("/design")
async def put_design_profile(file: UploadFile, request: Request,
                             tokens: str | None = Form(default=None),
                             me: Principal = Depends(require_admin)):
    filename, markdown = await _read_markdown(file, request)
    # Confirmed tokens are stored by **planting a fence into the original text**.
    # That is how the stored object stays a single "original markdown + metadata"
    # (no derived values stored alongside), which is why a file downloaded through
    # /raw is indistinguishable from a hand-written one on the next upload.
    #
    # If the file already has a fence, this field is **ignored** -- the fence is
    # authoritative. Otherwise a value the screen sent could overwrite what the admin
    # wrote by hand.
    if tokens is not None and not has_fence(markdown):
        confirmed = _confirmed_tokens(tokens)
        if confirmed:
            markdown = inject_fence(markdown, confirmed)
            # Re-measured after injection so we never end up rejecting, on
            # re-upload, a file we stored ourselves.
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
