# backend/aipds/preview_surface.py — prototypes are served from an origin separate from the app.
#
# **Why split the origins.** A prototype is code written by the build agent and is not
# trusted. Serving it from the same origin as the app exposes two things to that code:
#
#   - The app session cookie (`path=/`) is attached to prototype requests too. Next's
#     `/api` proxy turns that cookie into
#     `Authorization: Bearer <Cognito access token>`, so when an admin opens the
#     preview, the admin token ends up in the prototype server process.
#   - Because the prototype's JS is on the same origin, a plain `fetch('/api/…')` can
#     call the app API with the viewer's permissions.
#
# Stripping headers one by one plugs that hole; it does not remove it. A preview-only
# CloudFront distribution (infra/lib/aipds-preview-stack.ts) serves only `/api/proto/*`
# on the same EC2, and the app origin does not serve prototypes — the app cookie exists
# only on the app domain, so it **structurally** cannot reach the prototype
# (`*.cloudfront.net` is on the Public Suffix List, so the two distributions are
# different sites).
#
# How the backend knows **which surface a request came through**: only the preview
# distribution attaches the secret header `X-Preview-Verify` (separate from the
# `X-Origin-Verify` the app distribution attaches). nginx and the Next proxy pass this
# header through unchanged. Without knowing the value, it cannot be imitated via the app
# domain.
#
# The app origin serves prototypes when no configuration is present **only in local
# development with auth disabled** (`same_origin_allowed`). In that case there is no
# session cookie to steal. On a deployment with auth enabled, if the configuration is
# missing or the secret value cannot be read, the preview is closed — serving from the
# app origin in that state would let prototype code call the app API with the session of
# whoever opened the preview (admins included). The preview stack is always included in
# `cdk deploy --all`, so on a normal deployment this state lasts only for the few
# minutes before the harden sync and during a transient Secrets Manager outage (at most
# `_RETRY_AFTER` seconds).
from __future__ import annotations

import logging
import os
import secrets
import time

_log = logging.getLogger(__name__)

ORIGIN_ENV = "AIPDS_PREVIEW_ORIGIN"          # https://dXXXX.cloudfront.net
# The name of an environment variable, not a secret -- the `nosec` is for the name.
SECRET_ARN_ENV = "AIPDS_PREVIEW_SECRET_ARN"  # nosec B105 - Secrets Manager secret holding the header value
VERIFY_ENV = "AIPDS_PREVIEW_VERIFY"          # for passing the value directly (tests, local)
HEADER = "x-preview-verify"


def preview_origin() -> str | None:
    value = os.environ.get(ORIGIN_ENV, "").strip().rstrip("/")
    return value or None


#: Holds only successful values — caching a failure would let a single transient error
#: right after startup keep the preview surface off until the next restart.
_secret_cache: dict[str, str] = {}
#: Failures are remembered only briefly — so we do not hit Secrets Manager on every
#: request.
_RETRY_AFTER = 60.0
_failed_at: dict[str, float] = {}


def _secret_from_arn(arn: str) -> str | None:
    if arn in _secret_cache:
        return _secret_cache[arn]
    if time.monotonic() - _failed_at.get(arn, -_RETRY_AFTER) < _RETRY_AFTER:
        return None
    import boto3
    try:
        client = boto3.client("secretsmanager",
                              region_name=os.environ.get("AWS_REGION"))
        value = client.get_secret_value(SecretId=arn)["SecretString"].strip()
        if value:
            _secret_cache[arn] = value
        return value or None
    except Exception:
        # If it cannot be read, it is as if there were no preview surface (with auth
        # enabled, the preview is closed). Passing over it silently would leave no way
        # to tell "why the preview won't open", so we log it.
        _log.exception("reading the preview verify secret failed")
        _failed_at[arn] = time.monotonic()
        return None


def preview_verify() -> str | None:
    direct = os.environ.get(VERIFY_ENV, "").strip()
    if direct:
        return direct
    arn = os.environ.get(SECRET_ARN_ENV, "").strip()
    return _secret_from_arn(arn) if arn else None


def enabled() -> bool:
    return preview_origin() is not None and preview_verify() is not None


def same_origin_allowed() -> bool:
    """Whether the app origin may serve prototypes when there is no preview surface —
    only when auth is disabled."""
    import aipds.app as app_module
    return app_module.cognito_config() is None


def on_preview_surface(headers) -> bool:
    """Whether this request came through the preview distribution. If the preview is
    off, `same_origin_allowed()`."""
    expected = preview_verify()
    if preview_origin() is None or expected is None:
        return same_origin_allowed()
    presented = headers.get(HEADER, "")
    return bool(presented) and secrets.compare_digest(presented, expected)
