# backend/aipds/routes/proto_public.py — PUBLIC prototype preview proxy.
#
# This file is split out from prototypes.py because of authentication: app.py
# attaches the auth dependency at router-include time, and these routes have to
# remain outside Cognito auth. Someone who receives a validation survey link
# (/survey/{token}) has to be able to use the prototype without an account.
#
# The file boundary IS the auth boundary. A route added here is published to the
# internet.
#
# ---- Access tokens (one per prototype) ----
#
# "Unauthenticated" does not mean "unrestricted". This file used to let in anyone who
# knew a pid and a slug, and recorded that as a known limitation. That defence was
# effectively nonexistent: the pid is **a string the user types in** when creating a
# project (routes/projects.py's CreateProject.project_id) and the slug comes from a
# spec filename -- both human-readable and therefore guessable.
#
# Now each prototype has a token and the flow is two steps:
#
#   1. GET /proto/t/{token}  -- the gate. Translates the token into (pid, slug), sets
#      a cookie scoped to that prototype's path only, and 307s to the real preview
#      path.
#   2. /proto/{pid}/{slug}/* -- the proxy. Requires that cookie.
#
# **Why a cookie** rather than putting the token in the path: a prototype bakes its
# Next.js `basePath` at build time (proto/host.py's start), and that value ends up in
# asset URLs, client-router hrefs and its own redirects. Changing the path shape would
# require rebuilding every prototype already built (npm install + build, several
# minutes) and would carry the token out in asset URLs and Referer headers. A cookie
# is attached to asset requests automatically, so basePath needs no change at all.
#
# **Why a different cookie name per prototype**: a shared cookie with
# Path=/api/proto would let someone holding one prototype's link guess another
# prototype's pid/slug and walk in -- reproducing, one layer in, exactly the hole this
# is closing.
#
# **Why failures are 404** rather than 403: a 403 announces "there is something
# here". The point of this feature is not to be discoverable, so it is
# indistinguishable from a prototype that does not exist. The same judgement as
# surveys_public.py's `_resolve`, which does not distinguish a missing token from a
# missing survey. Diagnostics go to the log, not to the response.
#
# ⚠️ Remaining limitation (intended): nothing stops the recipient of a link from
# resharing it. Preventing that needs expiry or per-person tokens, and both are outside
# this feature's threat model (an outsider hunting for the URL by guessing). The premise
# that prototypes hold no sensitive data still applies.
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import (PlainTextResponse, RedirectResponse,
                                 StreamingResponse)

_log = logging.getLogger(__name__)

router = APIRouter()

# Hop-by-hop request headers never forwarded upstream; x-origin-verify is the
# CloudFront->nginx shared secret and must not leak into prototype processes.
_STRIP_REQUEST_HEADERS = {"host", "x-origin-verify", "connection",
                          "keep-alive", "transfer-encoding"}
# Hop-by-hop response headers: the proxy re-frames the body itself.
_STRIP_RESPONSE_HEADERS = {"transfer-encoding", "connection", "keep-alive"}


def proxy_prefix(pid: str, slug: str) -> str:
    """The path prefix as THIS APP sees it — what the proxy routes on.

    Exported because it is also what the proxy forwards upstream (intact, so an
    app built with Next.js `basePath` matches it). For the value to bake into a
    build, use `public_base_path` instead: in production the browser's path
    carries an extra mount that never reaches here.
    """
    return f"/proto/{quote(pid)}/{quote(slug)}"


#: Where this API is mounted from the BROWSER's point of view. In production
#: nginx sends everything to Next, whose app/api/[...path]/route.ts strips
#: `/api` before forwarding to FastAPI — so a path this app sees as
#: `/proto/...` was `/api/proto/...` in the browser. Configurable because local
#: dev talks to :8000 directly with no mount at all (set it to "").
_PUBLIC_PREFIX_ENV = "AIPDS_PUBLIC_PATH_PREFIX"
_PUBLIC_PREFIX_DEFAULT = "/api"


def public_base_path(pid: str, slug: str) -> str:
    """The prototype's prefix as the BROWSER sees it — the build input.

    Next.js bakes `basePath` into asset URLs at build time, and those URLs are
    resolved by the browser, so this must include the `/api` mount that
    `proxy_prefix` deliberately omits. Getting this wrong is not a redirect
    that self-corrects: assets 404 against the CloudFront root and the page
    renders unstyled and inert.
    """
    import os
    mount = os.environ.get(_PUBLIC_PREFIX_ENV, _PUBLIC_PREFIX_DEFAULT).rstrip("/")
    return f"{mount}{proxy_prefix(pid, slug)}"


#: The cookie name prefix. The frontend proxy's allowlist decides by this prefix
#: (forwardableCookies in frontend/lib/api/proxyAuth.ts), so both sides must agree --
#: if they diverge the cookie never reaches the backend and every preview 404s.
COOKIE_PREFIX = "aipds_proto_"


def cookie_name(pid: str, slug: str) -> str:
    """The cookie name unique to this prototype.

    pid/slug are not used verbatim for two reasons: they can contain characters that
    are invalid in a cookie name (the pid is an arbitrary user-typed string), and their
    length is uncontrolled. The hash is for naming rather than secrecy, so it is
    truncated to 16 characters -- a collision would make two prototypes share a cookie
    name, but the value is still compared against that path's own token
    (`_authorized`), so a collision does not hand over access.
    """
    digest = hashlib.sha256(f"{pid}/{slug}".encode("utf-8")).hexdigest()
    return f"{COOKIE_PREFIX}{digest[:16]}"


#: Whether to mark the access cookie `Secure`. Turned on for deployments served
#: over HTTPS.
#:
#: Why a **boolean naming exactly this one behaviour** rather than a stage name: a
#: name that matches what it does keeps the next person from misreading its scope. An
#: "environment" variable ends up standing for log format and error verbosity too, and
#: then there is no way to express a configuration that is "not production but does
#: need Secure" -- such as local verification behind an HTTPS proxy. Putting a stage
#: value ("production" and so on) into this variable is exactly the mistake this guards
#: against: if the name and the stage happen to be different strings, it ships silently
#: switched off.
_COOKIE_SECURE_ENV = "AIPDS_COOKIE_SECURE"
_TRUTHY = {"1", "true", "yes", "on"}


def _cookie_secure() -> bool:
    """Whether to mark the access cookie `Secure`.

    The default is **off**. Local development runs on `http://localhost` and browsers
    do not store Secure cookies over plain HTTP, so defaulting to on would leave
    previews unopenable in a development environment started with no configuration.

    The cost of that default is that omitting this variable in a deployment ships
    non-Secure cookies **with no symptom** (CloudFront enforces HTTPS, so nothing looks
    different). That is not the kind of thing an eye catches, so
    `infra/test/user-data.assert.ts` asserts the value is present in the deployment
    configuration -- it was omitted once for real.
    """
    return os.environ.get(_COOKIE_SECURE_ENV, "").strip().lower() in _TRUTHY


def _authorized(request: Request, pid: str, slug: str) -> bool:
    """Does this request carry this prototype's cookie?

    The comparison is against **that prototype's own token**. Matching the cookie name
    alone does not pass, so a collision in the cookie-name hash still cannot get into
    another prototype.
    """
    import aipds.app as app_module
    presented = request.cookies.get(cookie_name(pid, slug))
    if not presented:
        return False
    expected = app_module.proto_host().token_for(pid, slug)
    if not expected:
        # A prototype with no token has never been hosted -- there is no criterion
        # to admit it against, so it is refused. Returning True here would make "the
        # token file went missing" the same thing as "no authentication required".
        return False
    return secrets.compare_digest(presented, expected)


def _not_found() -> PlainTextResponse:
    """One response shared by "no such prototype" and "not authorised".

    The wording has to be identical too: differing bodies would defeat the point of
    matching the status codes.
    """
    return PlainTextResponse("not found", status_code=404)


@router.get("/proto/t/{token}")
async def enter_prototype(token: str):
    """The entry point for a token link. Sets the cookie and sends the browser to the
    real preview path.

    This route being a single GET is deliberate: only the top-level navigation a
    participant clicks in a chat arrives here, and every request after it (assets, the
    prototype's own API calls, form POSTs) goes through the existing proxy path
    carrying the cookie.

    The cookie's Path has to be the **browser-facing path** (`public_base_path`).
    Writing the path this app sees (`/proto/...`) means the browser does not attach the
    cookie to `/api/proto/...` requests -- because the proxy strips `/api` before the
    request reaches this app. The gate then behaves like a 200 while every request
    after it 404s.
    """
    import aipds.app as app_module
    target = app_module.proto_host().resolve_token(token)
    if target is None:
        _log.debug("proto gate 404: unknown token")
        return _not_found()
    pid, slug = target

    info = app_module.proto_host().status(pid, slug)
    if info is None or info.state != "running" or info.port is None:
        # The token is valid but hosting is off. Distinguishing this as a 502 is
        # fine here: only someone holding a valid token sees it, so it tells a prober
        # nothing. Not distinguishing it would instead leave the PM who handed out the
        # link unable to tell "wrong link" from "hosting is off".
        _log.debug("proto gate 502: not running (%s/%s)", pid, slug)
        return PlainTextResponse(
            "prototype not running — start hosting first", status_code=502)

    base = public_base_path(pid, slug)
    # 307 because this must not be cached: the response carries a Set-Cookie, and a
    # cached 301/308 would replay the redirect without the cookie, producing a 404
    # loop.
    response = RedirectResponse(f"{base}/", status_code=307)
    response.set_cookie(
        cookie_name(pid, slug),
        app_module.proto_host().ensure_token(pid, slug),
        path=base,
        httponly=True,       # The prototype app's JS has no reason to read its own
                             # access token. That code was written by the build agent
                             # and is not trusted.
        secure=_cookie_secure(),
        # lax is enough: a participant clicking a link in a chat is a top-level
        # navigation. strict would drop the cookie on exactly that click and break the
        # first entry.
        samesite="lax",
        # No max_age = a session cookie. The threat model is not about expiry, so no
        # lifetime is invented -- it disappears when the browser closes and the link can
        # be clicked again.
    )
    return response


def access_url_path(token: str) -> str:
    """The token gate's path -- **as the browser sees it**.

    It includes the `/api` mount for the same reason as `public_base_path`: this value
    becomes the link a participant pastes into a browser, so giving the path this app
    sees (`/proto/t/...`) would 404 at the CloudFront root.

    The server sends it down rather than having the frontend assemble it because the
    token is not client state -- for the frontend to build the URL it would have to
    receive the token first, and then the token would exist somewhere other than the
    link (a separate field in the list response, and the client state holding it).
    """
    mount = os.environ.get(_PUBLIC_PREFIX_ENV, _PUBLIC_PREFIX_DEFAULT).rstrip("/")
    return f"{mount}/proto/t/{quote(token)}"


def _rewritten_location(value: str, pid: str, slug: str) -> str:
    """Rewrite an upstream redirect target into a proxy-relative path.

    The prototype process only knows its own origin (127.0.0.1:<port>), so a
    redirect it issues — or one Starlette issues for a missing trailing slash
    — would send the browser straight at an internal address it cannot reach
    ("localhost:8000/proto/..." and then a hang). Reduce any absolute URL that
    points at the upstream to its path, and express every path under this
    prototype's proxy prefix. An off-site absolute redirect is left alone.
    """
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        # Only rewrite self-references; a redirect to a genuinely external
        # host must survive untouched.
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            return value
        path, query = parsed.path, parsed.query
    else:
        path, query = parsed.path, parsed.query
    # The BROWSER-side prefix: this Location header is for the browser, and an
    # app built with basePath emits that same value in its own redirects.
    prefix = public_base_path(pid, slug)
    if not path.startswith("/"):
        # Relative target: the browser resolves it against the current URL,
        # which is already inside the prefix — leave it as-is.
        return value
    # A prototype built with `basePath` emits redirects that ALREADY carry the
    # prefix; prepending it again would send the browser to
    # /api/proto/{pid}/{slug}/api/proto/{pid}/{slug}/... Match on a path-segment
    # boundary, not a bare startswith: "/api/proto/{pid}/{slug}-other" is a
    # DIFFERENT prototype and still needs the prefix.
    if path == prefix or path.startswith(f"{prefix}/"):
        out = path
    else:
        out = f"{prefix}{path}"
    return f"{out}?{query}" if query else out


# The slash-less form gets its own route so Starlette never emits its default
# ABSOLUTE 307 (which named this server's own origin and walked the browser off
# the public host onto localhost:8000, where it hung).
#
# It answers with a RELATIVE redirect that adds the trailing slash rather than
# serving the index in place: prototype HTML references assets relatively
# (href="styles.css"), and at ".../{slug}" (no slash) the browser resolves
# those against ".../{pid}/" — dropping the slug and 502ing every asset. Adding
# the slash makes the document's base ".../{slug}/", so relative refs land
# inside the prototype. Non-GET/HEAD methods are proxied through unchanged, so
# a form POST to the bare path still works.
@router.api_route("/proto/{pid}/{slug}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_prototype_root(pid: str, slug: str, request: Request):
    # Authorise before redirecting. Reversed, an unauthenticated request would still
    # get a 307, and that response alone would reveal that this pid/slug exists -- the
    # very fact the 404 exists to hide.
    if not _authorized(request, pid, slug):
        _log.debug("proto proxy 404: no valid cookie (%s/%s)", pid, slug)
        return _not_found()
    if request.method in ("GET", "HEAD"):
        target = f"{request.url.path}/"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        # 307 (not 308): keep it non-cacheable so a stale browser cache can't
        # pin this path shape if the routing ever changes.
        return RedirectResponse(target, status_code=307)
    return await proxy_prototype(pid, slug, "", request)


@router.api_route("/proto/{pid}/{slug}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_prototype(pid: str, slug: str, path: str, request: Request):
    # Authorise before checking hosting state: the split between 502 and 404 is
    # itself enough to reveal that a prototype exists, so an unauthenticated request
    # has to end as a 404 before reaching that branch.
    if not _authorized(request, pid, slug):
        _log.debug("proto proxy 404: no valid cookie (%s/%s)", pid, slug)
        return _not_found()
    import aipds.app as app_module
    info = app_module.proto_host().status(pid, slug)
    if info is None or info.state != "running" or info.port is None:
        _log.debug("proto proxy 502: not running (%s/%s)", pid, slug)
        return PlainTextResponse(
            "prototype not running — start hosting first", status_code=502)

    # Forward under the BROWSER-side prefix rather than stripping to "/". The
    # prototype is built with Next.js `basePath` = public_base_path, and
    # basePath changes URL generation AND request matching together, so the app
    # both emits and expects that prefix. Note this app never receives the
    # browser's `/api` mount (Next's route handler strips it), so forwarding
    # requires putting it back -- hence public_base_path, not proxy_prefix.
    #
    # Stripping left two broken halves: built without basePath the app's asset
    # URLs resolved against the CloudFront root (/_next/static/... -> 404);
    # built with it, every stripped request 404'd inside the app. (assetPrefix
    # alone would fix only /_next/ URLs, leaving public/ files and
    # client-router hrefs pointing at the root.)
    url = f"http://127.0.0.1:{info.port}{public_base_path(pid, slug)}/{path}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _STRIP_REQUEST_HEADERS}
    req = client.build_request(request.method, url,
                               params=request.query_params,
                               content=request.stream(),
                               headers=headers)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        _log.debug("proto proxy 502: upstream not responding (%s/%s)", pid, slug)
        return PlainTextResponse(
            "prototype not responding — check hosting logs", status_code=502)

    async def _close() -> None:
        await upstream.aclose()
        await client.aclose()

    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in _STRIP_RESPONSE_HEADERS}
    # A prototype that redirects (SPA route normalization, auth bounce, its own
    # trailing-slash handling) names its own internal origin — rewrite it so the
    # browser stays on the public proxy path.
    if "location" in resp_headers:
        resp_headers["location"] = _rewritten_location(
            resp_headers["location"], pid, slug)
    return StreamingResponse(upstream.aiter_raw(),
                             status_code=upstream.status_code,
                             headers=resp_headers,
                             background=BackgroundTask(_close))
