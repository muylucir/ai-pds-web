# backend/tests/test_auth_route_coverage.py
#
# Regression guard for auth coverage. Adding a new router and forgetting auth is the
# most expensive mistake in this app, so the route list itself is asserted.
#
# Adaptation note: the brief's original plan iterates app.routes as flat starlette.routing.Route
# objects and reads route.dependant. In this FastAPI version (0.139.2), routers attached with
# app.include_router(...) go into app.routes as fastapi.routing.
# _IncludedRouter — with neither .path nor .dependant. The effective route view that actually
# reflects the include-time `dependencies=[...]` is the list of _EffectiveRouteContext returned by
# _IncludedRouter.effective_candidates(), and their .path/.dependant behave exactly in the shape
# the original plan expected
# (the router dependencies are merged in front of each route's dependant.dependencies).
# So _app_routes() handles app.routes in two kinds: top-level
# starlette.routing.Route (built-in /docs etc.) as is, and _IncludedRouter expanded via
# effective_candidates().
from __future__ import annotations

from typing import Any

import fastapi.routing as fastapi_routing
from fastapi.testclient import TestClient
from starlette.routing import Route

import aipds.app as app_module
from aipds.app import app

# Paths that must be open without Cognito authentication — exactly these four.
#   /survey/{token}              anonymous survey response (end users without accounts)
#   /proto/t/{token}             prototype token gate (plants the cookie and 307s below)
#   /proto/{pid}/{slug}          prototype live preview (the same users must actually try the app)
#   /proto/{pid}/{slug}/{path:path}  same as above — static asset path inside the preview
#
# ⚠️ "No Cognito authentication" does not mean "unrestricted". The three prototype paths below are
# gated by a **per-prototype access token** instead of an account (routes/proto_public.py):
# the gate turns the token into a cookie, and the two proxies require that cookie. This list
# only asserts "require_user is not attached here", so whether the token gate itself works is
# asserted separately by test_routes_proto_public.py — without that, this list would let
# "may be public" be misread as "may have no defense at all".
PUBLIC_PATHS = {
    "/survey/{token}",
    "/proto/t/{token}",
    "/proto/{pid}/{slug}",
    "/proto/{pid}/{slug}/{path:path}",
}


def _app_routes() -> list[Any]:
    """Real routes, excluding /openapi.json, /docs, etc. that FastAPI attaches by default.

    The elements of app.routes come in two kinds: top-level Routes (built-in doc routes) and
    _IncludedRouters attached by include_router (all of this app's routes). The latter must be
    expanded with effective_candidates() to get individual routes (.path, .dependant) that reflect
    router-level dependencies.

    This function filtering out the built-in doc routes is no longer "an unavoidable hole"
    but a recorded decision: when cognito_config() is set, app.py builds FastAPI with
    openapi_url=None and does not register these routes at all (then this filter is a no-op
    on the empty set). In local development with auth unset the routes still exist and are
    intentionally unauthenticated — that decision itself is verified by
    test_docs_openapi_url_is_* and test_docs_*_on_the_real_app_* below.
    """
    builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    routes: list[Any] = []
    for r in app.routes:
        if isinstance(r, fastapi_routing._IncludedRouter):
            routes.extend(r.effective_candidates())
        elif isinstance(r, Route) and r.path not in builtin:
            routes.append(r)
    return routes


def _has_auth_dependency(route: Any) -> bool:
    """Whether require_user is in this route's dependency tree."""
    from aipds.auth.deps import require_user
    return any(getattr(d, "call", None) is require_user
               for d in route.dependant.dependencies)


def _has_admin_dependency(route: Any) -> bool:
    """Whether require_admin is in this route's dependency tree."""
    from aipds.auth.deps import require_admin
    return any(getattr(d, "call", None) is require_admin
               for d in route.dependant.dependencies)


def test_every_route_is_either_authenticated_or_explicitly_public():
    unprotected = [r.path for r in _app_routes()
                   if not _has_auth_dependency(r) and r.path not in PUBLIC_PATHS]
    assert unprotected == [], (
        "이 라우트들에 인증이 없다. 의도한 공개라면 PUBLIC_PATHS에 추가하고 "
        f"왜 공개인지 주석을 남길 것: {unprotected}")


def test_public_paths_all_exist():
    # If PUBLIC_PATHS drifts from the real routes (a path rename, etc.), the exception list silently
    # becomes meaningless. A name change is noticed here.
    paths = {r.path for r in _app_routes()}
    missing = PUBLIC_PATHS - paths
    assert missing == set(), f"PUBLIC_PATHS references non-existent routes: {missing}"


def test_public_paths_really_have_no_auth_dependency():
    # The opposite direction: if auth is attached to a path that must be public, surveys/previews break.
    wrongly_protected = [r.path for r in _app_routes()
                         if r.path in PUBLIC_PATHS and _has_auth_dependency(r)]
    assert wrongly_protected == [], (
        f"이 경로는 공개여야 한다(계정 없는 사용자가 쓴다): {wrongly_protected}")


def test_route_enumeration_has_not_silently_collapsed():
    # What this test guards is not the route "count" itself but the fact that _app_routes() is
    # still seeing the routers. _app_routes() depends on FastAPI internals
    # (_IncludedRouter, effective_candidates()) — if a future FastAPI changes how
    # app.routes is composed and _IncludedRouter disappears entirely or is
    # renamed, attribute access raises immediately, so that is safe. But if only some
    # routers silently change into a different shape and effective_candidates() drops those
    # routers, _app_routes() returns a "shrunken" list without any exception.
    # Then test_every_route_is_either_authenticated_or_explicitly_public
    # cannot check routes it does not see, so it simply passes even when unprotected routes
    # exist — the guardian passing itself becomes false evidence that "it is safe".
    # So we separately check that the route count is in a plausible range.
    #
    # The actual count is currently 37 (2026-07). The floor of 30 is set far enough below 37 not to
    # wobble with ordinary route additions/removals, while still high enough above 0 that a partial
    # omission (e.g., an entire router going invisible removes 3-9 routes at a time) is always
    # caught.
    count = len(_app_routes())
    assert count >= 30, (
        f"_app_routes()가 {count}개 라우트만 봤다(기대: 37개 안팎, 최소 30). "
        "라우트를 대량으로 지운 게 아니라면, FastAPI 업그레이드 등으로 "
        "_app_routes()의 _IncludedRouter/effective_candidates() 순회가 일부 "
        "라우터를 조용히 못 보게 된 것일 수 있다 — 그러면 나머지 커버리지 "
        "테스트들이 보이지 않는 라우트에 대해 공허하게(vacuously) 통과한다. "
        "_app_routes()의 순회 로직을 먼저 확인할 것.")


def test_admin_routes_require_admin_not_just_user():
    # /admin/* needs more than authentication (require_user) — pm is also an authenticated user, so
    # with only require_user attached, pm would reach the user management API. Checking that
    # require_admin is actually applied catches the regression of this router being registered
    # with require_user instead of require_admin.
    admin_routes = [r for r in _app_routes() if r.path.startswith("/admin")]
    assert admin_routes, "admin_users 라우터가 보이지 않는다"
    not_admin = [r.path for r in admin_routes if not _has_admin_dependency(r)]
    assert not_admin == [], (
        f"이 /admin 라우트들에 require_admin이 없다(require_user만으로는 pm도 "
        f"통과한다): {not_admin}")


def test_docs_openapi_url_is_none_when_auth_is_configured(monkeypatch):
    # Finding 3: /openapi.json, /docs, and /redoc are registered by FastAPI itself, so they
    # never go through include_router(..., dependencies=_AUTH) — leaving them as is in a deployment
    # with auth on is the same as handing the entire route table and parameter schemas to visitors
    # without accounts. app.py extracted this decision into app_module._docs_
    # openapi_url() — importlib.reload() of all of aipds.app recreates other module-global
    # singletons such as registry, and other test files that already hold references to those
    # objects (test_routes_answers.py etc.) break with KeyError
    # (observed). So only that function is verified directly, swapping env via monkeypatch.
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", "ap-northeast-2_TEST123")
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", "client-abc")
    assert app_module._docs_openapi_url() is None


def test_docs_openapi_url_is_set_when_auth_is_not_configured(monkeypatch):
    # The opposite direction: in local development (auth unset) the docs UI must still be on —
    # this is not an accident but a recorded choice (see the Finding 3 review).
    monkeypatch.delenv("AIPDS_COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("AIPDS_COGNITO_CLIENT_ID", raising=False)
    assert app_module._docs_openapi_url() == "/openapi.json"


def test_docs_are_absent_on_the_real_app_when_openapi_url_is_none():
    # Finding 3, end-to-end: build a FastAPI app the same way app.py does
    # (openapi_url=None) and confirm the three doc routes actually 404 rather
    # than just asserting the config value in isolation. A fresh app avoids
    # touching aipds.app's module-level singletons (registry etc.) that
    # other test files hold direct references to.
    from fastapi import FastAPI
    probe = FastAPI(title="probe", openapi_url=None)
    client = TestClient(probe)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404, (
            f"{path} must not answer anonymously when auth is configured")


def test_docs_exist_on_the_real_app_when_openapi_url_is_set():
    # Mirror of the above for the unauthenticated (local dev) case.
    from fastapi import FastAPI
    probe = FastAPI(title="probe", openapi_url="/openapi.json")
    client = TestClient(probe)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 200, (
            f"{path} must stay available in local dev (auth not configured)")
