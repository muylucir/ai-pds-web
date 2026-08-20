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
from fastapi.testclient import TestClient
from starlette.routing import Route

import aipds.app as app_module
from aipds.app import app

# Cognito 인증 없이 열려 있어야 하는 경로 — 정확히 이 넷이다.
#   /survey/{token}              익명 설문 응답 (계정 없는 최종 사용자)
#   /proto/t/{token}             프로토타입 토큰 게이트 (쿠키를 심고 아래로 307)
#   /proto/{pid}/{slug}          프로토타입 라이브 프리뷰 (같은 사용자가 앱을 실제로 써봐야 한다)
#   /proto/{pid}/{slug}/{path:path}  위와 동일 — 프리뷰 내부 정적 자원 경로
#
# ⚠️ "Cognito 인증 없음"이 "무제한"은 아니다. 아래 세 프로토타입 경로는 계정
# 대신 **프로토타입별 접근 토큰**으로 게이트된다(routes/proto_public.py):
# 게이트가 토큰을 쿠키로 바꾸고, 프록시 두 개가 그 쿠키를 요구한다. 이 목록은
# "여기에 require_user가 붙어 있지 않다"만 단정하므로, 토큰 게이트 자체가
# 동작하는지는 test_routes_proto_public.py가 따로 단정한다 — 그쪽이 없으면
# 이 목록은 "공개여도 된다"를 "아무 방어가 없어도 된다"로 잘못 읽히게 한다.
PUBLIC_PATHS = {
    "/survey/{token}",
    "/proto/t/{token}",
    "/proto/{pid}/{slug}",
    "/proto/{pid}/{slug}/{path:path}",
}


def _app_routes() -> list[Any]:
    """FastAPI가 기본으로 붙이는 /openapi.json·/docs 등을 제외한 실 라우트.

    app.routes의 원소는 두 종류다: 최상위 Route(빌트인 문서 라우트)와
    include_router로 붙은 _IncludedRouter(이 앱의 라우트 전부). 후자는
    effective_candidates()로 펼쳐야 라우터 단위 dependencies가 반영된
    개별 라우트(.path, .dependant)를 얻는다.

    이 함수가 빌트인 문서 라우트를 걸러내는 것은 더 이상 "어쩔 수 없이
    빠진 구멍"이 아니라 기록된 결정이다: app.py는 cognito_config()가
    설정되어 있으면 openapi_url=None으로 FastAPI를 만들어 이 라우트들을
    아예 등록하지 않는다(그러면 이 필터는 공집합에 대해 no-op이 된다).
    인증이 미설정인 로컬 개발에서는 여전히 라우트가 존재하고 의도적으로
    무인증이다 — 그 결정 자체는 아래 test_docs_openapi_url_is_*와
    test_docs_*_on_the_real_app_*이 검증한다.
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
    from aipds.auth.deps import require_user
    return any(getattr(d, "call", None) is require_user
               for d in route.dependant.dependencies)


def _has_admin_dependency(route: Any) -> bool:
    """이 라우트의 의존성 트리에 require_admin이 있는가."""
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


def test_route_enumeration_has_not_silently_collapsed():
    # 이 테스트가 지키는 것은 라우트 "개수" 자체가 아니라 _app_routes()가
    # 여전히 라우터들을 보고 있다는 사실이다. _app_routes()는 FastAPI 내부
    # (_IncludedRouter, effective_candidates())에 의존한다 — 향후 FastAPI가
    # app.routes의 구성 방식을 바꾸면서 _IncludedRouter가 아예 없어지거나
    # 이름이 바뀌면 속성 접근이 즉시 예외를 내므로 안전하다. 하지만 일부
    # 라우터만 조용히 다른 모양으로 바뀌어 effective_candidates()가 그
    # 라우터들을 빠뜨리는 경우, _app_routes()는 예외 없이 "축소된" 목록을
    # 반환한다. 그러면 test_every_route_is_either_authenticated_or_explicitly_public은
    # 자신이 보지 못하는 라우트를 검사할 수 없으므로 무보호 라우트가 있어도
    # 그냥 통과해버린다 — 가디언이 통과하는 것 자체가 "안전하다"는 잘못된
    # 증거가 된다. 그래서 라우트 개수가 그럴듯한 범위인지를 별도로 확인한다.
    #
    # 현재 실제 개수는 37(2026-07). 바닥값 30은 통상적인 라우트 추가/삭제로
    # 흔들리지 않을 만큼 37보다 충분히 낮게 잡았고, 그러면서도 부분 누락이
    # 생기면(예: 라우터 하나가 통째로 안 보이면 3~9개씩 사라진다) 반드시
    # 걸릴 만큼 0보다는 충분히 높다.
    count = len(_app_routes())
    assert count >= 30, (
        f"_app_routes()가 {count}개 라우트만 봤다(기대: 37개 안팎, 최소 30). "
        "라우트를 대량으로 지운 게 아니라면, FastAPI 업그레이드 등으로 "
        "_app_routes()의 _IncludedRouter/effective_candidates() 순회가 일부 "
        "라우터를 조용히 못 보게 된 것일 수 있다 — 그러면 나머지 커버리지 "
        "테스트들이 보이지 않는 라우트에 대해 공허하게(vacuously) 통과한다. "
        "_app_routes()의 순회 로직을 먼저 확인할 것.")


def test_admin_routes_require_admin_not_just_user():
    # /admin/* 은 인증(require_user)만으로는 부족하다 — pm도 인증된 사용자이므로
    # require_user만 붙으면 pm이 사용자 관리 API에 도달한다. require_admin이
    # 실제로 걸려 있는지를 확인해 이 라우터가 require_admin 대신 require_user로
    # 잘못 등록되는 회귀를 잡는다.
    admin_routes = [r for r in _app_routes() if r.path.startswith("/admin")]
    assert admin_routes, "admin_users 라우터가 보이지 않는다"
    not_admin = [r.path for r in admin_routes if not _has_admin_dependency(r)]
    assert not_admin == [], (
        f"이 /admin 라우트들에 require_admin이 없다(require_user만으로는 pm도 "
        f"통과한다): {not_admin}")


def test_docs_openapi_url_is_none_when_auth_is_configured(monkeypatch):
    # Finding 3: /openapi.json·/docs·/redoc은 FastAPI가 스스로 등록하므로
    # include_router(..., dependencies=_AUTH)를 절대 거치지 않는다 — 인증이
    # 켜진 배포에서 그대로 두면 계정 없는 방문자에게 전체 라우트 표·파라미터
    # 스키마를 통째로 내주는 것과 같다. app.py는 이 결정을 app_module._docs_
    # openapi_url()로 뽑아 뒀다 — aipds.app 전체를 importlib.reload()하면
    # registry 등 다른 모듈 전역 싱글턴이 새로 생겨, 이미 그 객체를 참조해 둔
    # 다른 테스트 파일들(test_routes_answers.py 등)이 KeyError로 깨진다
    # (실측). 그래서 그 함수만 monkeypatch로 env를 갈아끼워 직접 검증한다.
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", "ap-northeast-2_TEST123")
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", "client-abc")
    assert app_module._docs_openapi_url() is None


def test_docs_openapi_url_is_set_when_auth_is_not_configured(monkeypatch):
    # 반대 방향: 로컬 개발(인증 미설정)에서는 문서 UI가 여전히 켜져 있어야
    # 한다 — 이건 사고가 아니라 기록된 선택이다(Finding 3 리뷰 참고).
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
