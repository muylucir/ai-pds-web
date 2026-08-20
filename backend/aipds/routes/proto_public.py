# backend/aipds/routes/proto_public.py — PUBLIC prototype preview proxy.
#
# 이 파일이 prototypes.py에서 분리된 이유는 인증이다: app.py는 라우터 include
# 시점에 인증 의존성을 붙이는데, 이 라우트들은 Cognito 인증 없이 남아야 한다.
# 검증 설문 링크(/survey/{token})를 받은 사용자는 계정이 없는 상태로 프로토타입을
# 써야 한다.
#
# 파일 경계 = 인증 경계. 여기에 라우트를 추가하면 그것은 인터넷에 공개된다.
#
# ---- 접근 토큰 (프로토타입마다 하나) ----
#
# "무인증"이 "무제한"을 뜻하지는 않는다. 이 파일은 예전에 pid와 slug를 아는
# 사람이면 누구나 들어올 수 있었고, 그 사실을 알려진 한계로 적어 두고 있었다.
# 그 방어선은 실제로는 없는 것과 같았다: pid는 프로젝트 생성 시 **사용자가 직접
# 넣는 문자열**이고(routes/projects.py의 CreateProject.project_id) slug는 스펙
# 파일명에서 온다 — 둘 다 사람이 읽을 수 있고 따라서 추측할 수 있다.
#
# 지금은 프로토타입마다 토큰이 있고 흐름은 두 단계다:
#
#   1. GET /proto/t/{token}  — 게이트. 토큰을 (pid, slug)로 번역하고, 그
#      프로토타입 경로에만 스코프된 쿠키를 심고, 실제 프리뷰 경로로 307한다.
#   2. /proto/{pid}/{slug}/* — 프록시. 그 쿠키를 요구한다.
#
# **왜 쿠키인가**(경로에 토큰을 박지 않고): 프로토타입은 Next.js `basePath`를
# 빌드 시점에 굽고(proto/host.py의 start), 그 값이 asset URL·클라이언트 라우터
# href·자체 리다이렉트에 전부 들어간다. 경로 모양을 바꾸면 이미 빌드된 모든
# 프로토타입을 재빌드해야 하고(npm install + build, 수 분) 토큰이 asset URL과
# Referer에 실려 나간다. 쿠키는 asset 요청에도 자동으로 붙으므로 basePath는
# 건드릴 필요가 없다.
#
# **왜 프로토타입마다 다른 쿠키 이름인가**: 공용 쿠키에 Path=/api/proto를 주면
# 한 프로토타입 링크를 받은 사람이 다른 프로토타입의 pid/slug를 추측해서 들어갈
# 수 있다 — 지금 막으려는 구멍이 한 겹 안쪽에서 그대로 재현된다.
#
# **왜 실패가 404인가**(403이 아니라): 403은 "여기 뭔가 있다"를 알려준다. 이
# 기능의 목적은 발견되지 않는 것이므로 없는 프로토타입과 구별하지 않는다.
# surveys_public.py의 `_resolve`가 없는 토큰과 없는 설문을 구별하지 않는 것과
# 같은 판단이다. 진단은 응답이 아니라 로그로 남긴다.
#
# ⚠️ 남아 있는 한계(의도된 것): 링크를 받은 사람이 그것을 재공유하는 것은 막지
# 않는다. 그것을 막으려면 만료나 개인별 토큰이 필요하고, 둘 다 이 기능의 위협
# 모델(URL을 추측으로 찾는 외부인) 밖이다. 프로토타입에 민감 데이터를 넣지
# 않는다는 전제는 계속 유효하다.
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


#: 쿠키 이름의 접두어. 프론트 프록시의 허용목록이 이 접두어로 판정하므로
#: (frontend/lib/api/proxyAuth.ts의 forwardableCookies) 양쪽이 같아야 한다 —
#: 어긋나면 쿠키가 백엔드에 닿지 않고 모든 프리뷰가 404가 된다.
COOKIE_PREFIX = "pf_proto_"


def cookie_name(pid: str, slug: str) -> str:
    """이 프로토타입 전용 쿠키 이름.

    pid/slug를 그대로 넣지 않는 이유는 두 가지다: 쿠키 이름에 쓸 수 없는 문자가
    섞일 수 있고(pid는 사용자가 넣는 임의 문자열이다), 길이도 통제되지 않는다.
    해시는 비밀이 아니라 이름을 짓기 위한 것이므로 16자로 자른다 — 충돌하면 두
    프로토타입이 쿠키를 공유하게 되지만, 값 검증은 여전히 그 경로의 토큰과
    비교하므로(`_authorized`) 충돌이 접근 권한을 넘겨주지는 않는다.
    """
    digest = hashlib.sha256(f"{pid}/{slug}".encode("utf-8")).hexdigest()
    return f"{COOKIE_PREFIX}{digest[:16]}"


#: 접근 쿠키에 `Secure`를 붙일지. HTTPS로 서비스되는 배포에서 켠다.
#:
#: 스테이지 이름(`AIPDS_ENV=production`)이 아니라 **이 동작 하나만 가리키는
#: 불리언**인 이유: 이름이 하는 일과 정확히 같아야 다음 사람이 범위를 오해하지
#: 않는다. "환경" 변수는 로그 포맷·에러 상세 같은 것까지 묶어 부르게 되고, 그러면
#: HTTPS 프록시를 앞에 둔 로컬 검증처럼 "프로덕션은 아니지만 Secure는 필요한"
#: 구성을 표현할 수 없다.
_COOKIE_SECURE_ENV = "AIPDS_COOKIE_SECURE"
_TRUTHY = {"1", "true", "yes", "on"}


def _cookie_secure() -> bool:
    """접근 쿠키에 `Secure`를 붙일지.

    기본값은 **꺼짐**이다. 로컬 개발이 `http://localhost`이고 브라우저는 평문
    HTTP에서 Secure 쿠키를 저장하지 않으므로, 기본이 켜짐이면 아무 설정 없이
    띄운 개발 환경에서 프리뷰가 열리지 않는다.

    그 기본값의 대가는 배포에서 이 변수를 빠뜨리면 **증상 없이** non-Secure
    쿠키가 나가는 것이다(CloudFront가 HTTPS를 강제하므로 화면상 아무 차이가
    없다). 눈으로 잡을 수 없는 종류이므로 `infra/test/user-data.assert.ts`가
    배포 설정에 이 값이 있는지 단정한다 -- 실제로 한 번 빠뜨렸다.
    """
    return os.environ.get(_COOKIE_SECURE_ENV, "").strip().lower() in _TRUTHY


def _authorized(request: Request, pid: str, slug: str) -> bool:
    """이 요청이 이 프로토타입의 쿠키를 갖고 있는가.

    비교 대상은 **그 프로토타입의 토큰**이다. 쿠키 이름만 맞는 것으로는 통과하지
    못하므로, 쿠키 이름 해시가 충돌해도 다른 프로토타입에 들어갈 수 없다.
    """
    import aipds.app as app_module
    presented = request.cookies.get(cookie_name(pid, slug))
    if not presented:
        return False
    expected = app_module.proto_host().token_for(pid, slug)
    if not expected:
        # 토큰이 없는 프로토타입은 아직 호스팅된 적이 없다 — 통과시킬 기준이
        # 없으므로 거절한다. 여기서 True를 주면 토큰 파일이 사라진 상태가
        # 곧 무인증 상태가 된다.
        return False
    return secrets.compare_digest(presented, expected)


def _not_found() -> PlainTextResponse:
    """존재하지 않는 프로토타입과 인증되지 않은 접근에 대한 같은 응답.

    문구도 같아야 한다 — 본문이 다르면 응답 코드를 맞춰 놓은 의미가 없다.
    """
    return PlainTextResponse("not found", status_code=404)


@router.get("/proto/t/{token}")
async def enter_prototype(token: str):
    """토큰 링크의 진입점. 쿠키를 심고 실제 프리뷰 경로로 보낸다.

    이 라우트가 GET 하나뿐인 것은 의도된 것이다: 참가자가 채팅에서 클릭하는
    top-level 네비게이션만 여기로 들어오고, 그 뒤의 모든 요청(asset, 프로토타입
    자체의 API 호출, 폼 POST)은 쿠키를 들고 기존 프록시 경로로 간다.

    쿠키의 Path는 **브라우저 관점 경로**(`public_base_path`)여야 한다. 이 앱이
    보는 경로(`/proto/...`)로 쓰면 브라우저는 `/api/proto/...` 요청에 쿠키를
    붙이지 않는다 — 프록시가 `/api`를 떼고 나서야 이 앱에 닿기 때문이다.
    그러면 게이트는 200처럼 동작하는데 그다음 요청이 전부 404가 된다.
    """
    import aipds.app as app_module
    target = app_module.proto_host().resolve_token(token)
    if target is None:
        _log.debug("proto gate 404: unknown token")
        return _not_found()
    pid, slug = target

    info = app_module.proto_host().status(pid, slug)
    if info is None or info.state != "running" or info.port is None:
        # 토큰은 유효한데 호스팅이 꺼져 있다. 여기서는 502로 구별해도 된다 —
        # 유효한 토큰을 가진 사람에게만 보이는 정보이므로 프로버에게 아무것도
        # 알려주지 않는다. 오히려 구별하지 않으면 링크를 나눠 준 PM이 "링크가
        # 틀렸나"와 "호스팅이 꺼졌나"를 구별할 수 없다.
        _log.debug("proto gate 502: not running (%s/%s)", pid, slug)
        return PlainTextResponse(
            "prototype not running — start hosting first", status_code=502)

    base = public_base_path(pid, slug)
    # 307: 캐시되지 않아야 한다. 이 응답에는 Set-Cookie가 실려 있고, 301/308이
    # 캐시되면 쿠키 없이 리다이렉트만 재생되어 404 루프가 된다.
    response = RedirectResponse(f"{base}/", status_code=307)
    response.set_cookie(
        cookie_name(pid, slug),
        app_module.proto_host().ensure_token(pid, slug),
        path=base,
        httponly=True,       # 프로토타입 앱의 JS가 자기 접근 토큰을 읽을 이유가
                             # 없다. 그 코드는 빌드 에이전트가 쓴 것이고 신뢰
                             # 대상이 아니다.
        secure=_cookie_secure(),
        # lax: 참가자가 채팅 링크를 누르는 것이 top-level 네비게이션이므로
        # 충분하다. strict면 바로 그 클릭에서 쿠키가 빠져 첫 진입이 깨진다.
        samesite="lax",
        # max_age 없음 = 세션 쿠키. 위협 모델이 "만료"가 아니므로 수명을
        # 발명하지 않는다 — 브라우저를 닫으면 사라지고 링크를 다시 누르면 된다.
    )
    return response


def access_url_path(token: str) -> str:
    """토큰 게이트의 경로 — **브라우저 관점**이다.

    `public_base_path`와 같은 이유로 `/api` 마운트를 포함한다: 이 값은 참가자가
    브라우저에 붙여 넣는 링크가 되므로, 이 앱이 보는 경로(`/proto/t/...`)를 주면
    CloudFront 루트에서 404가 된다.

    프론트가 조립하지 않고 서버가 내려보내는 이유는 토큰이 클라이언트 상태가
    아니기 때문이다 — 프론트가 URL을 만들려면 토큰을 먼저 받아야 하고, 그러면
    토큰이 링크가 아닌 곳(목록 응답의 별도 필드, 그리고 그것을 담은 클라이언트
    상태)에도 존재하게 된다.
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
    # 리다이렉트보다 먼저 검증한다. 순서를 뒤집으면 인증 없는 요청도 307을
    # 받으므로, 그 응답만으로 "이 pid/slug가 존재한다"를 알 수 있다 —
    # 404로 감추려는 것이 바로 그 사실이다.
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
    # 호스팅 상태 확인보다 먼저 검증한다: 502와 404가 갈리는 것만으로도
    # 프로토타입의 존재를 알 수 있으므로, 인증되지 않은 요청은 그 분기에
    # 닿기 전에 404로 끝나야 한다.
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
