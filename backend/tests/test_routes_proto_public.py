# backend/tests/test_routes_proto_public.py
#
# 프로토타입 접근 토큰 게이트. test_auth_route_coverage.py는 이 경로들에
# require_user가 **없다**는 것만 단정하므로, "그러면 아무 방어도 없다"가 되지
# 않게 하는 것이 이 파일의 역할이다.
#
# 여기서 지키는 성질:
#   - pid/slug를 알아도 쿠키 없이는 들어올 수 없다 (원래의 구멍)
#   - 한 프로토타입의 쿠키가 다른 프로토타입에 통하지 않는다 (Path 스코프의 근거)
#   - 실패는 전부 404로 수렴한다 (존재를 알려주지 않는다)
#   - 쿠키의 Path가 브라우저 관점 경로와 정확히 일치한다 (틀리면 조용히 다 깨진다)
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.proto.host import TOKEN_FILENAME, HostInfo, ProtoHost
from aipds.routes.proto_public import (cookie_name, public_base_path)

client = TestClient(app_module.app)

PID = "proj-1"
SLUG = "demo"
OTHER_SLUG = "other"


class StubHost:
    """실제 ProtoHost의 토큰 부분만 위임하고 프로세스 관리는 흉내낸다.

    토큰 로직은 **실물을 쓴다**(디스크에 파일을 쓰는 그 코드) — 여기서 가짜
    dict로 대체하면 이 파일이 단정하려는 것이 전부 스텁의 동작이 된다.
    """

    def __init__(self, root):
        self._real = ProtoHost(root=root)
        self.infos: dict[tuple[str, str], HostInfo] = {}

    # --- 토큰: 실물에 위임 ---
    def ensure_token(self, pid, slug):
        return self._real.ensure_token(pid, slug)

    def token_for(self, pid, slug):
        return self._real.token_for(pid, slug)

    def resolve_token(self, token):
        return self._real.resolve_token(token)

    def load_tokens(self):
        return self._real.load_tokens()

    # --- 호스팅 상태: 테스트가 직접 세운다 ---
    def status(self, pid, slug):
        return self.infos.get((pid, slug))

    def log_tail(self, pid, slug, lines=100):
        return ""

    def sweep_orphans(self):
        # app.py의 lifespan이 부른다. 없으면 기동 시 AttributeError가 로그에
        # 남아, 이 파일에서 정작 봐야 할 실패를 가린다.
        return 0


@pytest.fixture()
def env(monkeypatch, tmp_path):
    host = StubHost(root=tmp_path)
    monkeypatch.setattr(app_module, "proto_host", lambda: host)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    # 게이트/프록시가 계산하는 브라우저 관점 프리픽스를 배포와 같게 고정한다.
    monkeypatch.setenv("AIPDS_PUBLIC_PATH_PREFIX", "/api")
    return {"host": host, "root": tmp_path}


def _running(env, pid=PID, slug=SLUG, port=4001):
    env["host"].infos[(pid, slug)] = HostInfo(
        state="running", port=port, log_tail="")


# ---- 원래의 구멍 ----

def test_proxy_without_a_cookie_is_404_even_when_running(env):
    """이것이 이 기능의 존재 이유다.

    예전에는 pid와 slug를 아는 사람이면 누구나 프리뷰를 열 수 있었고, pid는
    사용자가 직접 넣는 문자열이라 사실상 추측 가능했다. 호스팅이 실제로 돌고
    있어도 쿠키 없이는 404여야 한다 — 502(꺼져 있음)조차 알려주면 안 된다.
    """
    _running(env)
    resp = client.get(f"/proto/{PID}/{SLUG}/index.html")
    assert resp.status_code == 404


def test_proxy_root_without_a_cookie_is_404_not_a_redirect(env):
    """슬래시 없는 형태도 막힌다.

    이 라우트는 인증 검사보다 먼저 307을 내보낼 수 있는 모양이었다. 그러면
    리다이렉트를 받았다는 사실만으로 "이 pid/slug가 존재한다"를 알 수 있어,
    404로 감추려던 것이 그대로 새어 나간다.
    """
    _running(env)
    resp = client.get(f"/proto/{PID}/{SLUG}", follow_redirects=False)
    assert resp.status_code == 404


def test_an_unknown_token_is_404(env):
    resp = client.get("/proto/t/nope-not-a-real-token")
    assert resp.status_code == 404


def test_a_cookie_for_one_prototype_does_not_open_another(env):
    """Path 스코프 결정의 핵심 단정.

    쿠키를 프로토타입마다 나누지 않고 /api/proto 하나로 두면, 한 링크를 받은
    참가자가 다른 프로토타입의 slug를 추측해 들어갈 수 있다 — 막으려던 구멍이
    한 겹 안쪽에서 재현된다. 브라우저의 Path 스코프가 1차 방어선이지만, 그것은
    선의의 브라우저만 지키므로 서버도 같은 판단을 해야 한다.
    """
    _running(env, slug=SLUG)
    _running(env, slug=OTHER_SLUG)
    # SLUG의 쿠키를 들고 OTHER_SLUG를 노린다. 쿠키 이름이 달라 애초에 붙지
    # 않지만, 이름을 OTHER_SLUG의 것으로 바꿔 값만 재사용하는 경우까지 막아야
    # 한다 — 아래가 그 경우다.
    stolen = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/{PID}/{OTHER_SLUG}/index.html",
                      headers={"Cookie": f"{cookie_name(PID, OTHER_SLUG)}={stolen}"})
    assert resp.status_code == 404


def test_a_prototype_that_never_hosted_has_no_token_so_nothing_passes(env):
    """토큰 파일이 없으면 통과 기준이 없다 — 아무 값으로도 열리지 않아야 한다.

    여기서 통과시키면 "토큰이 사라진 상태"가 곧 "무인증 상태"가 된다.
    """
    _running(env)
    resp = client.get(f"/proto/{PID}/{SLUG}/index.html",
                      headers={"Cookie": f"{cookie_name(PID, SLUG)}=anything"})
    assert resp.status_code == 404


# ---- 게이트 ----

def test_the_gate_sets_a_cookie_and_redirects_into_the_prototype(env):
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)

    assert resp.status_code == 307
    base = public_base_path(PID, SLUG)
    assert resp.headers["location"] == f"{base}/"
    assert cookie_name(PID, SLUG) in resp.cookies


def test_the_cookie_path_is_the_browser_visible_prefix(env):
    """Path가 브라우저 관점 경로여야 한다.

    이 앱이 보는 경로(`/proto/...`)로 쓰면 브라우저는 `/api/proto/...` 요청에
    쿠키를 붙이지 않는다 — 프록시가 `/api`를 떼고 나서야 이 앱에 닿기 때문이다.
    그러면 게이트는 성공한 것처럼 보이는데 그다음 요청이 전부 404가 되고, 원인이
    쿠키 Path라는 것은 화면에 전혀 드러나지 않는다.
    """
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)

    set_cookie = resp.headers["set-cookie"]
    assert f"Path={public_base_path(PID, SLUG)}" in set_cookie
    # 브라우저 관점이므로 반드시 /api 마운트를 포함한다.
    assert "Path=/api/proto/" in set_cookie


def test_the_cookie_is_httponly_and_lax(env):
    """HttpOnly: 프로토타입 앱의 JS가 자기 접근 토큰을 읽을 이유가 없다. 그 코드는
    빌드 에이전트가 쓴 것이고 신뢰 대상이 아니다.

    SameSite=Lax: 참가자가 채팅 링크를 누르는 top-level 네비게이션에 쿠키가
    실려야 한다. Strict면 바로 그 첫 클릭에서 빠진다.
    """
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)

    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    # max-age/expires 없음 = 세션 쿠키. 수명을 발명하지 않는다는 결정을 고정한다.
    assert "max-age" not in set_cookie
    assert "expires" not in set_cookie


def test_the_cookie_is_not_secure_by_default(env):
    """기본값은 꺼짐이다 — 로컬 개발(http://localhost)에서 Secure를 붙이면
    브라우저가 쿠키를 저장하지 않아 프리뷰가 아예 열리지 않는다."""
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert "secure" not in resp.headers["set-cookie"].lower()


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", " true "])
def test_cookie_secure_env_accepts_truthy_values(env, monkeypatch, value):
    """`AIPDS_COOKIE_SECURE`가 켜지면 Secure가 붙는다.

    여러 표기를 받는 이유: 이 값을 쓰는 곳이 systemd 유닛 파일과 셸 env라
    `true`/`1`/`yes`가 모두 자연스럽게 나온다. 하나만 받으면 나머지를 쓴 사람은
    켰다고 믿지만 실제로는 꺼져 있고, 그 실패는 화면에 드러나지 않는다.
    """
    monkeypatch.setenv("AIPDS_COOKIE_SECURE", value)
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert "secure" in resp.headers["set-cookie"].lower()


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "production"])
def test_cookie_secure_env_rejects_non_truthy_values(env, monkeypatch, value):
    """켜지지 않는 값들. `production`이 여기 있는 것이 의도다 — 이 변수는
    스테이지 이름이 아니라 Secure 스위치 하나만 가리키는 불리언이므로, 스테이지
    값을 그대로 넣는 실수가 조용히 통과하면 이름을 좁힌 목적이 사라진다."""
    monkeypatch.setenv("AIPDS_COOKIE_SECURE", value)
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert "secure" not in resp.headers["set-cookie"].lower()


def test_the_gate_502s_when_hosting_is_off(env):
    """유효한 토큰인데 호스팅이 꺼져 있으면 502.

    여기서만 404와 구별한다. 유효한 토큰을 가진 사람에게만 보이는 정보이므로
    프로버에게 아무것도 알려주지 않고, 구별하지 않으면 링크를 나눠 준 PM이
    "링크가 틀렸나"와 "호스팅이 꺼졌나"를 구별할 수 없다.
    """
    token = env["host"].ensure_token(PID, SLUG)   # 호스팅은 세우지 않는다
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert resp.status_code == 502
    assert "start hosting first" in resp.text


def test_the_gate_then_the_proxy_works_end_to_end(env, monkeypatch):
    """게이트가 심은 쿠키가 실제로 프록시를 통과해야 한다.

    두 곳이 쿠키 이름·값을 따로 계산하므로(게이트는 `set_cookie`, 프록시는
    `_authorized`) 한쪽만 바꾸면 조용히 어긋난다. 쿠키 잼을 가진 클라이언트로
    왕복시켜 그 연결을 고정한다.

    **`AIPDS_PUBLIC_PATH_PREFIX=""`로 도는 것이 이 테스트의 조건이다.**
    배포값("/api")에서는 쿠키의 Path가 `/api/proto/...`인데 이 앱이 받는 경로는
    `/proto/...`이므로, httpx의 쿠키 잼이 (브라우저와 똑같이, 그리고 올바르게)
    쿠키를 보내지 않는다. 배포에서는 브라우저가 `/api/proto/...`로 보내고 Next
    프록시가 그 헤더를 전달하므로 성립한다 — 그 계층은 프론트의
    proxyAuth.test.ts가 담당한다. 여기서 검증할 수 있는 것은 마운트가 없는
    구성(로컬 개발이 실제로 쓰는 값)의 왕복이고, 그것이 이름·값의 정합성을
    확인하는 데는 충분하다.
    """
    monkeypatch.setenv("AIPDS_PUBLIC_PATH_PREFIX", "")
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)

    with TestClient(app_module.app) as jar_client:
        gate = jar_client.get(f"/proto/t/{token}", follow_redirects=False)
        assert gate.status_code == 307
        assert gate.headers["location"] == f"/proto/{PID}/{SLUG}/"

        # 쿠키가 잼에 남았으므로 이제 프록시가 인증을 통과해야 한다. upstream이
        # 없으므로 502("not responding")까지 가는 것이 성공 신호다 — 404가
        # 아니라는 것이 요점이다.
        resp = jar_client.get(f"/proto/{PID}/{SLUG}/index.html")
        assert resp.status_code == 502
        assert "not found" not in resp.text


# ---- 토큰 수명 ----

def test_ensure_token_is_stable_across_calls(env):
    """stop -> start를 반복해도 값이 그대로여야 한다.

    워크숍 중 호스팅을 껐다 켜는 것 때문에 이미 나눠 준 링크가 죽으면, 증상은
    "10분 전에 되던 URL이 404"로 나타난다.
    """
    first = env["host"].ensure_token(PID, SLUG)
    second = env["host"].ensure_token(PID, SLUG)
    assert first == second


def test_tokens_survive_a_restart(env):
    """새 ProtoHost가 디스크에서 토큰을 되살린다.

    인메모리 맵만 있으면 백엔드 재시작이 배포된 링크를 전부 무효화하고, 다시
    호스팅해도 복구되지 않는다 — 참가자 URL 안의 토큰은 바뀌지 않으므로.
    """
    token = env["host"].ensure_token(PID, SLUG)

    reborn = ProtoHost(root=env["root"])
    assert reborn.resolve_token(token) is None   # 아직 읽지 않았다
    assert reborn.load_tokens() == 1
    assert reborn.resolve_token(token) == (PID, SLUG)


async def test_purge_revokes_the_token(env):
    """리셋이 링크 폐기 경로다.

    파일만 지우고 인메모리 맵을 남기면, 사용자가 지웠다고 믿는 프로토타입의
    토큰이 프로세스 수명 내내 계속 해소된다 — 즉 리셋이 실제로는 폐기하지 않는다.
    """
    real = env["host"]._real
    (env["root"] / PID / SLUG).mkdir(parents=True, exist_ok=True)
    token = real.ensure_token(PID, SLUG)
    assert real.resolve_token(token) == (PID, SLUG)

    await real.purge(PID, SLUG)

    assert real.resolve_token(token) is None
    assert not (env["root"] / PID / SLUG / TOKEN_FILENAME).exists()


def test_load_tokens_skips_an_empty_token_file(env):
    """빈 토큰 파일이 빈 문자열 토큰으로 등록되면 안 된다.

    등록되면 `resolve_token("")`이 성공하고, 빈 쿠키를 보내는 요청이 통과한다.
    """
    target = env["root"] / PID / SLUG
    target.mkdir(parents=True, exist_ok=True)
    (target / TOKEN_FILENAME).write_text("", encoding="utf-8")

    host = ProtoHost(root=env["root"])
    assert host.load_tokens() == 0
    assert host.resolve_token("") is None
