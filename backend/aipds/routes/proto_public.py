# backend/aipds/routes/proto_public.py — PUBLIC prototype preview proxy.
#
# This file is split out of prototypes.py because of authentication: app.py attaches
# the auth dependency when it includes a router, but these routes must stay free of
# Cognito authentication. A user who receives a validation survey link
# (/survey/{token}) has to use the prototype without an account.
#
# File boundary = auth boundary. Any route added here is exposed to the internet.
#
# ---- Access tokens (one per prototype) ----
#
# "Unauthenticated" does not mean "unrestricted". This file used to let in anyone who
# knew the pid and slug, and it recorded that as a known limitation. That line of
# defense was effectively nonexistent: the pid is a **string the user types in** when
# creating the project (CreateProject.project_id in routes/projects.py), and the slug
# comes from the spec filename -- both are human-readable and therefore guessable.
#
# Now each prototype has a token, and the flow has two steps:
#
#   1. GET /proto/t/{token}  — the gate. Translates the token into (pid, slug), sets a
#      cookie scoped to that prototype's path only, and 307s to the real preview path.
#   2. /proto/{pid}/{slug}/* — the proxy. Requires that cookie.
#
# **Why a cookie** (rather than embedding the token in the path): a prototype bakes
# the Next.js `basePath` in at build time (start in proto/host.py), and that value
# goes into every asset URL, client-router href, and its own redirects. Changing the
# path shape would mean rebuilding every prototype already built (npm install + build,
# several minutes), and the token would leak out in asset URLs and the Referer. A
# cookie is attached to asset requests automatically, so basePath does not need to
# change.
#
# **Why a different cookie name per prototype**: if a shared cookie got
# Path=/api/proto, someone who received one prototype's link could guess another
# prototype's pid/slug and get in -- the very hole being closed here would reappear
# one layer further in.
#
# **Why failure is 404** (not 403): a 403 says "something is here". The point of this
# feature is not to be discovered, so it does not distinguish this from a nonexistent
# prototype. This is the same judgment as `_resolve` in surveys_public.py, which does
# not distinguish a missing token from a missing survey. Diagnostics go to the log,
# not the response.
#
# ⚠️ Remaining limitation (intentional): this does not stop someone who received the
# link from re-sharing it. Stopping that would need expiry or per-person tokens, and
# both are outside this feature's threat model (an outsider finding the URL by
# guessing). The premise that prototypes hold no sensitive data still stands.
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import (PlainTextResponse, RedirectResponse,
                                 StreamingResponse)

from aipds import preview_surface
from aipds.proto.store import resolve_token as resolve_preview_token

_log = logging.getLogger(__name__)

router = APIRouter()

# There is one proxy upstream client per process (event loop). Creating one per
# request incurs the construction cost (SSL context and CA bundle load, measured at
# ~16ms) synchronously on the loop, with no keep-alive either -- a single page pulls
# dozens of assets, so the multiplied cost stalls every SSE stream at once.
#
# Two things to keep while sharing it:
#   - **Disable the cookie jar.** An httpx client stores a response's Set-Cookie in
#     its own jar and attaches it to later requests. That was harmless with a new
#     client per request, but when shared, a cookie a prototype set for one viewer
#     rides along on the next viewer's request. The browser's cookies are forwarded
#     as-is in the Cookie header, so no jar is needed.
#   - **Do not cap the number of connections.** Streaming responses (HMR, SSE) hold
#     connections for a long time. With a cap, they fill the pool and asset requests
#     queue behind them -- the previous setup (a client per request) had no cap either.
_proxy_client: httpx.AsyncClient | None = None
_proxy_client_loop: asyncio.AbstractEventLoop | None = None


def _proxy_http() -> httpx.AsyncClient:
    """The shared upstream client for the current event loop.

    Recreated when the loop changes -- the connection pool is bound to the loop that
    created it. In production there is one loop, so it is created only once; and in
    tests that start a new loop every time (TestClient), connections from a dead loop
    are not reused.
    """
    global _proxy_client, _proxy_client_loop
    loop = asyncio.get_running_loop()
    if _proxy_client is None or _proxy_client_loop is not loop:
        _proxy_client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=5.0),
            limits=httpx.Limits(max_connections=None,
                                max_keepalive_connections=20),
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])))
        _proxy_client_loop = loop
    return _proxy_client


async def aclose_proxy_http() -> None:
    """Called at lifespan shutdown -- closes open keep-alive connections."""
    global _proxy_client, _proxy_client_loop
    if _proxy_client is not None:
        await _proxy_client.aclose()
    _proxy_client = None
    _proxy_client_loop = None


# Hop-by-hop request headers never forwarded upstream; x-origin-verify and
# x-preview-verify are the CloudFront->nginx shared secrets and must not leak into
# prototype processes. `authorization` is the Next proxy's Cognito bearer — on the
# preview origin there is no app session to translate, and on a deployment without
# one (aipds/preview_surface.py) this is what keeps the viewer's token out of code
# nobody reviewed.
_STRIP_REQUEST_HEADERS = {"host", "x-origin-verify", "x-preview-verify",
                          "authorization", "connection", "keep-alive",
                          "transfer-encoding"}
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


#: Cookie name prefix. The frontend proxy's allowlist matches on this prefix
#: (forwardableCookies in frontend/lib/api/proxyAuth.ts), so both sides must agree --
#: if they diverge, the cookie never reaches the backend and every preview 404s.
COOKIE_PREFIX = "aipds_proto_"


def cookie_name(pid: str, slug: str) -> str:
    """The cookie name dedicated to this prototype.

    There are two reasons not to use pid/slug directly: they may contain characters
    not allowed in a cookie name (the pid is an arbitrary user-entered string), and
    their length is uncontrolled. The hash is for naming, not secrecy, so it is cut
    to 16 characters -- a collision would make two prototypes share a cookie, but the
    value is still compared against that path's token (`_authorized`), so a collision
    does not hand over access.
    """
    digest = hashlib.sha256(f"{pid}/{slug}".encode("utf-8")).hexdigest()
    return f"{COOKIE_PREFIX}{digest[:16]}"


#: Whether to add `Secure` to the access cookie. Turn on in deployments served
#: over HTTPS.
#:
#: Why a **boolean that names only this one behavior** rather than a stage name: the
#: name must match exactly what it does so the next person does not misread its
#: scope. An "environment" variable ends up pulling in things like log format and
#: error detail, and then a configuration that is "not production but needs Secure",
#: such as local validation behind an HTTPS proxy, cannot be expressed. Putting a
#: stage value ("production" etc.) into this variable as-is is exactly the mistake
#: this prevents -- if the name and stage happen to be different strings, it ships
#: silently turned off.
_COOKIE_SECURE_ENV = "AIPDS_COOKIE_SECURE"
_TRUTHY = {"1", "true", "yes", "on"}


def _cookie_secure() -> bool:
    """Whether to add `Secure` to the access cookie.

    The default is **off**. Local development runs on `http://localhost` and browsers
    do not store Secure cookies over plain HTTP, so with a default of on, previews
    would not open in a dev environment started without any configuration.

    The cost of that default is that if a deployment omits this variable, a non-Secure
    cookie goes out **with no symptom** (CloudFront enforces HTTPS, so nothing looks
    different on screen). Since this cannot be caught by eye,
    `infra/test/user-data.assert.ts` asserts that the deployment config sets this
    value -- it was in fact omitted once.
    """
    return os.environ.get(_COOKIE_SECURE_ENV, "").strip().lower() in _TRUTHY


def _authorized(request: Request, pid: str, slug: str) -> bool:
    """Whether this request carries this prototype's cookie.

    The comparison is against **that prototype's token**. A matching cookie name alone
    does not pass, so even a cookie-name hash collision cannot get into another
    prototype.
    """
    import aipds.app as app_module
    presented = request.cookies.get(cookie_name(pid, slug))
    if not presented:
        return False
    expected = app_module.proto_host().token_for(pid, slug)
    if not expected:
        # A prototype with no token has never been hosted -- there is nothing to
        # check against, so reject. Returning True here would make a missing token
        # file equivalent to no authentication.
        return False
    return secrets.compare_digest(presented, expected)


def _not_found() -> PlainTextResponse:
    """The same response for a nonexistent prototype and an unauthenticated access.

    The wording must match too -- if the bodies differ, matching the status codes is
    pointless.
    """
    return PlainTextResponse("not found", status_code=404)


async def _resolve_from_index(token: str) -> tuple[str, str] | None:
    """Look up a token missing from the cache in the S3 root index (proto/store.py).

    A replaced instance has no token files, so its cache is empty -- yet links already
    handed out must keep working. When found, the token is planted in the cache and the
    token file (`adopt_token`) so the subsequent cookie check (`_authorized`) sees the
    same value. Tokens of unregistered projects (being deleted, another instance) are
    not accepted.
    """
    import aipds.app as app_module
    if not app_module.durable_projects_enabled():
        return None
    try:
        target = await resolve_preview_token(app_module.surveys_root_s3_factory(), token)
    except Exception:
        _log.exception("preview token index lookup failed")
        return None
    if target is None or not app_module.registry.is_registered(target[0]):
        return None
    app_module.proto_host().adopt_token(token, *target)
    return target


@router.get("/proto/t/{token}")
async def enter_prototype(token: str, request: Request):
    """Entry point for a token link. Sets the cookie and sends to the real preview path.

    That this route is a single GET is intentional: only the top-level navigation a
    participant clicks in the chat comes in here, and every later request (assets, the
    prototype's own API calls, form POSTs) carries the cookie to the existing proxy
    path.

    The cookie's Path must be the **browser-side path** (`public_base_path`). If it
    used the path this app sees (`/proto/...`), the browser would not attach the
    cookie to `/api/proto/...` requests -- the request only reaches this app after the
    proxy strips `/api`. The gate would then behave like a 200 while every following
    request 404s.
    """
    import aipds.app as app_module
    if not preview_surface.on_preview_surface(request.headers):
        if not preview_surface.enabled():
            # Auth is on in this deployment but there is no preview surface (missing
            # config, failed secret read). Do not serve from the app origin (see the
            # header of aipds/preview_surface.py).
            _log.warning("proto gate 503: preview surface is not configured")
            return PlainTextResponse(
                "prototype preview is not available right now — try again shortly",
                status_code=503)
        # A link that arrived on the app domain (handed out before the preview surface
        # existed) is sent to the same path on the preview domain. No cookie is set --
        # there is no reason for a prototype cookie to exist on the app origin. Sending
        # without validating the token is fine because nothing leaks: whatever comes in
        # gets the same redirect.
        return RedirectResponse(
            f"{preview_surface.preview_origin()}{_access_path(token)}", status_code=307)
    target = app_module.proto_host().resolve_token(token)
    if target is None:
        target = await _resolve_from_index(token)
    if target is None:
        _log.debug("proto gate 404: unknown token")
        return _not_found()
    pid, slug = target

    info = app_module.proto_host().status(pid, slug)
    if info is None or info.state != "running" or info.port is None:
        # The token is valid but hosting is off. Distinguishing this with a 502 is fine
        # here -- only someone holding a valid token sees it, so it tells a prober
        # nothing. Conversely, without the distinction the PM who handed out the link
        # could not tell "is the link wrong?" from "is hosting off?".
        _log.debug("proto gate 502: not running (%s/%s)", pid, slug)
        return PlainTextResponse(
            "prototype not running — start hosting first", status_code=502)

    base = public_base_path(pid, slug)
    # 307: must not be cached. This response carries Set-Cookie, and if a 301/308 were
    # cached, only the redirect would replay without the cookie, causing a 404 loop.
    response = RedirectResponse(f"{base}/", status_code=307)
    response.set_cookie(
        cookie_name(pid, slug),
        app_module.proto_host().ensure_token(pid, slug),
        path=base,
        httponly=True,       # The prototype app's JS has no reason to read its own
                             # access token. That code was written by the build
                             # agent and is not trusted.
        secure=_cookie_secure(),
        # lax: enough, since a participant clicking the chat link is a top-level
        # navigation. With strict, the cookie would be dropped on that very click and
        # the first entry would break.
        samesite="lax",
        # No max_age = session cookie. The threat model is not about "expiry", so we do
        # not invent a lifetime -- it disappears when the browser closes, and clicking
        # the link again restores it.
    )
    return response


def _access_path(token: str) -> str:
    """The token gate's **browser-side path** (without the domain)."""
    mount = os.environ.get(_PUBLIC_PREFIX_ENV, _PUBLIC_PREFIX_DEFAULT).rstrip("/")
    return f"{mount}/proto/t/{quote(token)}"


def access_url_path(token: str) -> str:
    """The token gate's path -- **browser-side**.

    It includes the `/api` mount for the same reason as `public_base_path`: this value
    becomes the link a participant pastes into a browser, so giving the path this app
    sees (`/proto/t/...`) would 404 at the CloudFront root.

    The server sends it down instead of the frontend assembling it because the token
    is not client state -- for the frontend to build the URL it would first have to
    receive the token, and then the token would also exist outside the link (as a
    separate field in the list response, and in the client state holding it).
    """
    path = _access_path(token)
    # When a preview surface exists, the link is an **absolute URL on that domain** --
    # a relative path on the app domain would have the participant open the prototype
    # on the app origin (aipds/preview_surface.py).
    origin = preview_surface.preview_origin() if preview_surface.enabled() else None
    return f"{origin}{path}" if origin else path


#: The namespace of AI-PDS Web's own cookies (frontend lib/auth/cookies.ts and COOKIE_PREFIX above).
#: The Next proxy drops it on preview requests (forwardableCookies), so a prototype cookie with such a
#: name would never come back — and one named after an access cookie would shadow it.
_APP_COOKIE_PREFIX = "aipds_"


def _scoped_set_cookie(value: str, pid: str, slug: str) -> str | None:
    """A prototype's `Set-Cookie`, confined to that prototype. None to drop it.

    **Why the proxy decides, not the prototype.** Every prototype is served from one preview origin
    and told apart only by path, so a cookie's `Path` is the only wall between them. A `Path=/`
    cookie goes to every prototype on the domain: two persona demos using the same cookie name sign
    each other out, and one prototype's server reads another's cookies. The build rules ask for
    `Path=basePath` (proto-config/CLAUDE.md), but the code is generated and the build agent checks it
    without this proxy, so nothing would catch a miss — observed: an agent set `Path=/` while chasing
    a login that kept dropping (2026-10-27). Here it holds whatever the prototype wrote.

    - `Path` outside this prototype's prefix, or absent (the browser would default it to the request's
      directory, which can also sit outside a deep page), becomes the prefix. A path inside it is kept.
    - `Domain` is removed — a host-only cookie cannot reach another host, and the prototype only knows
      its loopback address anyway.
    - An `aipds_` name is dropped (see `_APP_COOKIE_PREFIX`).

    Cookies a page sets from JS (`document.cookie`) never pass through here; the build rules cover those.
    """
    first, _, rest = value.partition(";")
    name = first.split("=", 1)[0].strip()
    if not name or name.startswith(_APP_COOKIE_PREFIX):
        return None
    prefix = public_base_path(pid, slug)
    attrs: list[str] = []
    path: str | None = None
    for raw in rest.split(";"):
        attr = raw.strip()
        if not attr:
            continue
        key = attr.split("=", 1)[0].strip().lower()
        if key == "domain":
            continue
        if key == "path":
            path = attr.split("=", 1)[1].strip() if "=" in attr else ""
            continue
        attrs.append(attr)
    if path is None or not (path == prefix or path.startswith(f"{prefix}/")):
        path = prefix
    return "; ".join([first.strip(), f"Path={path}", *attrs])


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
    # When a preview surface exists, the app origin does not serve prototypes -- the
    # same 404 regardless of authentication (aipds/preview_surface.py).
    if not preview_surface.on_preview_surface(request.headers):
        return _not_found()
    # Validate before redirecting. With the order reversed, unauthenticated requests
    # would also get a 307, and that response alone would reveal "this pid/slug
    # exists" -- exactly the fact the 404 is meant to hide.
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
    if not preview_surface.on_preview_surface(request.headers):
        return _not_found()
    # Validate before checking hosting state: the 502-vs-404 split alone reveals that
    # the prototype exists, so an unauthenticated request must end in a 404 before it
    # reaches that branch.
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
    client = _proxy_http()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _STRIP_REQUEST_HEADERS}
    req = client.build_request(request.method, url,
                               params=request.query_params,
                               content=request.stream(),
                               headers=headers)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        _log.debug("proto proxy 502: upstream not responding (%s/%s)", pid, slug)
        return PlainTextResponse(
            "prototype not responding — check hosting logs", status_code=502)

    response = StreamingResponse(upstream.aiter_raw(),
                                 status_code=upstream.status_code,
                                 background=BackgroundTask(upstream.aclose))
    # Header by header, repeats kept. A dict (or httpx's `items()`) joins repeated headers
    # with ", " — harmless for most, but `Set-Cookie` cannot be joined: a login that sets a
    # session and a role cookie in one response became one header the browser reads as a
    # single cookie. (Behind Next's /api proxy, undici happens to split it back; the direct
    # path in local development did not.)
    for key, value in upstream.headers.multi_items():
        if key.lower() in _STRIP_RESPONSE_HEADERS:
            continue
        # A prototype that redirects (SPA route normalization, auth bounce, its own
        # trailing-slash handling) names its own internal origin — rewrite it so the
        # browser stays on the public proxy path.
        if key.lower() == "location":
            value = _rewritten_location(value, pid, slug)
        if key.lower() == "set-cookie":
            scoped = _scoped_set_cookie(value, pid, slug)
            if scoped is None:
                _log.debug("proto proxy dropped an app-namespace cookie (%s/%s)", pid, slug)
                continue
            value = scoped
        response.headers.append(key, value)
    return response
