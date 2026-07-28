# backend/pathfinder/routes/proto_public.py — PUBLIC prototype preview proxy.
#
# 이 파일이 prototypes.py에서 분리된 이유는 인증이다: app.py는 라우터 include
# 시점에 인증 의존성을 붙이는데, 이 두 라우트는 공개로 남아야 한다. 검증 설문
# 링크(/survey/{token})를 받은 사용자는 계정이 없는 상태로 프로토타입을 써야 한다.
#
# 파일 경계 = 인증 경계. 여기에 라우트를 추가하면 그것은 인터넷에 공개된다.
#
# ⚠️ 알려진 한계(의도된 것): pid와 slug를 아는 사람이면 누구나 접근할 수 있다.
# 방어선은 slug의 추측 난이도뿐인 얕은 보안이므로 프로토타입에 민감 데이터를
# 넣지 않는 것이 전제다.
from __future__ import annotations

import logging
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
_PUBLIC_PREFIX_ENV = "PATHFINDER_PUBLIC_PATH_PREFIX"
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
    import pathfinder.app as app_module
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
