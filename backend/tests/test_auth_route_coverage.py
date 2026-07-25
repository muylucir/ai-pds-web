# backend/tests/test_auth_route_coverage.py
#
# 인증 커버리지의 회귀 방지 장치. 새 라우터를 추가하면서 인증을 빠뜨리는 것이
# 이 앱에서 가장 값비싼 실수이므로, 라우트 목록 자체를 단정한다.
#
# 어댑테이션 노트: 브리프의 원안은 app.routes를 flat한 starlette.routing.Route로
# 순회하며 route.dependant를 읽는다. 이 FastAPI 버전(0.139.2)에서는
# app.include_router(...)로 붙인 라우터가 app.routes에 fastapi.routing.
# _IncludedRouter로 들어간다 — .path도 .dependant도 없다. include 시점의
# `dependencies=[...]`가 실제로 반영된 유효 라우트 뷰는
# _IncludedRouter.effective_candidates()가 반환하는 _EffectiveRouteContext
# 목록이며, 여기의 .path/.dependant는 원안이 기대한 모양과 동일하게 동작한다
# (라우터 dependencies가 각 라우트의 dependant.dependencies 앞쪽에 합쳐진다).
# 그래서 _app_routes()는 app.routes를 두 종류로 나눠 처리한다: 최상위
# starlette.routing.Route(빌트인 /docs 등)는 그대로, _IncludedRouter는
# effective_candidates()로 펼친다.
from __future__ import annotations

from typing import Any

import fastapi.routing as fastapi_routing
from starlette.routing import Route

from pathfinder.app import app

# 무인증으로 열려 있어야 하는 경로 — 정확히 이 셋이다.
#   /survey/{token}              익명 설문 응답 (계정 없는 최종 사용자)
#   /proto/{pid}/{slug}          프로토타입 라이브 프리뷰 (같은 사용자가 앱을 실제로 써봐야 한다)
#   /proto/{pid}/{slug}/{path:path}  위와 동일 — 프리뷰 내부 정적 자원 경로
PUBLIC_PATHS = {
    "/survey/{token}",
    "/proto/{pid}/{slug}",
    "/proto/{pid}/{slug}/{path:path}",
}


def _app_routes() -> list[Any]:
    """FastAPI가 기본으로 붙이는 /openapi.json·/docs 등을 제외한 실 라우트.

    app.routes의 원소는 두 종류다: 최상위 Route(빌트인 문서 라우트)와
    include_router로 붙은 _IncludedRouter(이 앱의 라우트 전부). 후자는
    effective_candidates()로 펼쳐야 라우터 단위 dependencies가 반영된
    개별 라우트(.path, .dependant)를 얻는다.
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
    """이 라우트의 의존성 트리에 require_user가 있는가."""
    from pathfinder.auth.deps import require_user
    return any(getattr(d, "call", None) is require_user
               for d in route.dependant.dependencies)


def test_every_route_is_either_authenticated_or_explicitly_public():
    unprotected = [r.path for r in _app_routes()
                   if not _has_auth_dependency(r) and r.path not in PUBLIC_PATHS]
    assert unprotected == [], (
        "이 라우트들에 인증이 없다. 의도한 공개라면 PUBLIC_PATHS에 추가하고 "
        f"왜 공개인지 주석을 남길 것: {unprotected}")


def test_public_paths_all_exist():
    # PUBLIC_PATHS가 실제 라우트와 어긋나면(경로 리네임 등) 예외 목록이 조용히
    # 무의미해진다. 이름이 바뀌면 여기서 알아차린다.
    paths = {r.path for r in _app_routes()}
    missing = PUBLIC_PATHS - paths
    assert missing == set(), f"PUBLIC_PATHS references non-existent routes: {missing}"


def test_public_paths_really_have_no_auth_dependency():
    # 반대 방향: 공개여야 하는 경로에 인증이 붙으면 설문/프리뷰가 깨진다.
    wrongly_protected = [r.path for r in _app_routes()
                         if r.path in PUBLIC_PATHS and _has_auth_dependency(r)]
    assert wrongly_protected == [], (
        f"이 경로는 공개여야 한다(계정 없는 사용자가 쓴다): {wrongly_protected}")
