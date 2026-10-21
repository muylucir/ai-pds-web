# backend/tests/test_routes_proto_public.py
#
# Prototype access token gate. test_auth_route_coverage.py only asserts that these paths
# have **no** require_user, so this file's role is to keep that from turning into
# "then there is no defense at all".
#
# Properties guarded here:
#   - knowing the pid/slug does not get you in without the cookie (the original hole)
#   - one prototype's cookie does not work for another prototype (the basis for Path scoping)
#   - every failure converges to 404 (existence is not revealed)
#   - the cookie's Path exactly matches the browser-side path (if wrong, everything breaks silently)
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
    """Delegates only the token part to the real ProtoHost and mimics process management.

    The token logic **uses the real thing** (the code that writes files to disk) — replacing it
    with a fake dict here would turn everything this file means to assert into stub behavior.
    """

    def __init__(self, root, state_root=None):
        self._real = ProtoHost(root=root, state_root=state_root)
        self.infos: dict[tuple[str, str], HostInfo] = {}

    # --- Tokens: delegated to the real thing ---
    def ensure_token(self, pid, slug):
        return self._real.ensure_token(pid, slug)

    def token_for(self, pid, slug):
        return self._real.token_for(pid, slug)

    def resolve_token(self, token):
        return self._real.resolve_token(token)

    def adopt_token(self, token, pid, slug):
        return self._real.adopt_token(token, pid, slug)

    def load_tokens(self):
        return self._real.load_tokens()

    # --- Hosting state: set up directly by the tests ---
    def status(self, pid, slug):
        return self.infos.get((pid, slug))

    def log_tail(self, pid, slug, lines=100):
        return ""

    def sweep_orphans(self):
        # Called by app.py's lifespan. Without it, an AttributeError at startup lands in
        # the log and obscures the failures this file actually needs to see.
        return 0


@pytest.fixture()
def env(monkeypatch, tmp_path):
    state = tmp_path.with_name(tmp_path.name + "-state")
    host = StubHost(root=tmp_path, state_root=state)
    monkeypatch.setattr(app_module, "proto_host", lambda: host)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    # Pin the browser-side prefix computed by the gate/proxy to match the deployment.
    monkeypatch.setenv("AIPDS_PUBLIC_PATH_PREFIX", "/api")
    return {"host": host, "root": tmp_path, "state": state}


def _running(env, pid=PID, slug=SLUG, port=4001):
    env["host"].infos[(pid, slug)] = HostInfo(
        state="running", port=port, log_tail="")


# ---- The original hole ----

def test_proxy_without_a_cookie_is_404_even_when_running(env):
    """This is why this feature exists.

    Previously, anyone who knew the pid and slug could open the preview, and the pid is a
    user-entered string, so it was effectively guessable. Even when hosting is actually running,
    it must be 404 without the cookie — not even a 502 (off) should be revealed.
    """
    _running(env)
    resp = client.get(f"/proto/{PID}/{SLUG}/index.html")
    assert resp.status_code == 404


def test_proxy_root_without_a_cookie_is_404_not_a_redirect(env):
    """The form without a trailing slash is blocked too.

    This route had a shape that could emit a 307 before the auth check. Then the mere fact of
    receiving a redirect would reveal "this pid/slug exists", leaking exactly what the 404
    was meant to hide.
    """
    _running(env)
    resp = client.get(f"/proto/{PID}/{SLUG}", follow_redirects=False)
    assert resp.status_code == 404


def test_an_unknown_token_is_404(env):
    resp = client.get("/proto/t/nope-not-a-real-token")
    assert resp.status_code == 404


def test_a_cookie_for_one_prototype_does_not_open_another(env):
    """The core assertion behind the Path scoping decision.

    If the cookie were not split per prototype but kept as a single /api/proto one, a participant
    who received one link could guess another prototype's slug and get in — the hole we meant to
    close reappears one layer inside. The browser's Path scope is the first line of defense, but
    it only protects well-behaved browsers, so the server must make the same judgment.
    """
    _running(env, slug=SLUG)
    _running(env, slug=OTHER_SLUG)
    # Holds SLUG's cookie and targets OTHER_SLUG. The cookie names differ, so it is not
    # attached in the first place, but we must also block the case where the name is changed
    # to OTHER_SLUG's and only the value is reused — below is that case.
    stolen = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/{PID}/{OTHER_SLUG}/index.html",
                      headers={"Cookie": f"{cookie_name(PID, OTHER_SLUG)}={stolen}"})
    assert resp.status_code == 404


def test_a_prototype_that_never_hosted_has_no_token_so_nothing_passes(env):
    """Without a token file there is no passing criterion — no value should open it.

    Letting it through here would make "the token is gone" equivalent to "no authentication".
    """
    _running(env)
    resp = client.get(f"/proto/{PID}/{SLUG}/index.html",
                      headers={"Cookie": f"{cookie_name(PID, SLUG)}=anything"})
    assert resp.status_code == 404


# ---- Gate ----

def test_the_gate_sets_a_cookie_and_redirects_into_the_prototype(env):
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)

    assert resp.status_code == 307
    base = public_base_path(PID, SLUG)
    assert resp.headers["location"] == f"{base}/"
    assert cookie_name(PID, SLUG) in resp.cookies


def test_the_cookie_path_is_the_browser_visible_prefix(env):
    """Path must be the browser-side path.

    If it were written with the path this app sees (`/proto/...`), the browser would not attach
    the cookie to `/api/proto/...` requests — because requests reach this app only after the
    proxy strips `/api`. Then the gate looks successful but every subsequent request becomes 404,
    and nothing on screen reveals that the cause is the cookie Path.
    """
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)

    set_cookie = resp.headers["set-cookie"]
    assert f"Path={public_base_path(PID, SLUG)}" in set_cookie
    # Since it is browser-side, it must include the /api mount.
    assert "Path=/api/proto/" in set_cookie


def test_the_cookie_is_httponly_and_lax(env):
    """HttpOnly: the prototype app's JS has no reason to read its own access token. That code
    was written by the build agent and is not trusted.

    SameSite=Lax: the cookie must be sent on the top-level navigation when a participant clicks
    a chat link. With Strict it would be dropped on exactly that first click.
    """
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)

    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    # No max-age/expires = session cookie. Pins the decision not to invent a lifetime.
    assert "max-age" not in set_cookie
    assert "expires" not in set_cookie


def test_the_cookie_is_not_secure_by_default(env):
    """The default is off — adding Secure in local development (http://localhost) makes the
    browser not store the cookie, so the preview does not open at all."""
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert "secure" not in resp.headers["set-cookie"].lower()


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", " true "])
def test_cookie_secure_env_accepts_truthy_values(env, monkeypatch, value):
    """When `AIPDS_COOKIE_SECURE` is on, Secure is added.

    Why several spellings are accepted: this value is written in systemd unit files and shell env,
    where `true`/`1`/`yes` all come naturally. Accepting only one would leave someone who wrote
    another believing it is on while it is actually off, and that failure does not show on screen.
    """
    monkeypatch.setenv("AIPDS_COOKIE_SECURE", value)
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert "secure" in resp.headers["set-cookie"].lower()


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "production"])
def test_cookie_secure_env_rejects_non_truthy_values(env, monkeypatch, value):
    """Values that do not turn it on. Having `production` here is intentional — this variable
    is a boolean that refers only to the Secure switch, not a stage name, so if the mistake of
    putting the stage value in passed silently, the purpose of narrowing the name would be lost."""
    monkeypatch.setenv("AIPDS_COOKIE_SECURE", value)
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert "secure" not in resp.headers["set-cookie"].lower()


def test_the_gate_502s_when_hosting_is_off(env):
    """A valid token while hosting is off gives 502.

    Only here is it distinguished from 404. It is information visible only to someone holding a
    valid token, so it tells a prober nothing, and without the distinction the PM who handed out
    the link could not tell "is the link wrong" from "is hosting off".
    """
    token = env["host"].ensure_token(PID, SLUG)   # hosting is not started
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert resp.status_code == 502
    assert "start hosting first" in resp.text


def test_the_gate_then_the_proxy_works_end_to_end(env, monkeypatch):
    """The cookie the gate plants must actually pass through the proxy.

    The two places compute the cookie name and value separately (the gate via `set_cookie`, the
    proxy via `_authorized`), so changing only one drifts silently. A round trip with a client
    that has a cookie jar pins that connection.

    **Running with `AIPDS_PUBLIC_PATH_PREFIX=""` is this test's precondition.**
    With the deployed value ("/api"), the cookie's Path is `/api/proto/...` while the path this
    app receives is `/proto/...`, so httpx's cookie jar (just like a browser, and correctly)
    does not send the cookie. In deployment it works because the browser sends to `/api/proto/...`
    and the Next proxy forwards that header — that layer is covered by the frontend's
    proxyAuth.test.ts. What can be verified here is the round trip in a configuration without the
    mount (the value local development actually uses), and that is enough to confirm the
    name/value consistency.
    """
    monkeypatch.setenv("AIPDS_PUBLIC_PATH_PREFIX", "")
    _running(env)
    token = env["host"].ensure_token(PID, SLUG)

    with TestClient(app_module.app) as jar_client:
        gate = jar_client.get(f"/proto/t/{token}", follow_redirects=False)
        assert gate.status_code == 307
        assert gate.headers["location"] == f"/proto/{PID}/{SLUG}/"

        # The cookie remained in the jar, so the proxy must now pass authentication. There is
        # no upstream, so reaching 502 ("not responding") is the success signal — the point is
        # that it is not 404.
        resp = jar_client.get(f"/proto/{PID}/{SLUG}/index.html")
        assert resp.status_code == 502
        assert "not found" not in resp.text


# ---- Token lifetime ----

def test_ensure_token_is_stable_across_calls(env):
    """The value must stay the same across repeated stop -> start.

    If links already handed out die because hosting was turned off and on during a workshop, the
    symptom shows up as "a URL that worked 10 minutes ago is 404".
    """
    first = env["host"].ensure_token(PID, SLUG)
    second = env["host"].ensure_token(PID, SLUG)
    assert first == second


def test_tokens_survive_a_restart(env):
    """A new ProtoHost restores tokens from disk.

    With only an in-memory map, a backend restart would invalidate every distributed link, and
    re-hosting would not recover them — because the token inside participants' URLs does not change.
    """
    token = env["host"].ensure_token(PID, SLUG)

    reborn = ProtoHost(root=env["root"], state_root=env["state"])
    assert reborn.resolve_token(token) is None   # not read yet
    assert reborn.load_tokens() == 1
    assert reborn.resolve_token(token) == (PID, SLUG)


async def test_purge_revokes_the_token(env):
    """Reset is the link revocation path.

    If only the file were deleted and the in-memory map kept, the token of a prototype the user
    believes they deleted would keep resolving for the life of the process — i.e., reset would
    not actually revoke.
    """
    real = env["host"]._real
    (env["root"] / PID / SLUG).mkdir(parents=True, exist_ok=True)
    token = real.ensure_token(PID, SLUG)
    assert real.resolve_token(token) == (PID, SLUG)

    await real.purge(PID, SLUG)

    assert real.resolve_token(token) is None
    assert not (env["state"] / PID / SLUG / "token").exists()


def test_load_tokens_skips_an_empty_token_file(env):
    """An empty token file must not be registered as an empty-string token.

    If it were, `resolve_token("")` would succeed and requests sending an empty cookie would pass.
    """
    for target, name in ((env["state"] / PID / SLUG, "token"),
                         (env["root"] / PID / "legacy", TOKEN_FILENAME)):
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text("", encoding="utf-8")

    host = ProtoHost(root=env["root"], state_root=env["state"])
    assert host.load_tokens() == 0
    assert host.resolve_token("") is None


# ---- Replaced instance: links survive even without a local token (proto/store.py) ----

def _index(monkeypatch, pid=PID, slug=SLUG, token="tok-from-s3"):  # nosec B107 - fixture token
    from aipds.proto.store import PrototypeStore
    from fakes.in_memory_s3 import FakeS3Store
    import asyncio
    root = FakeS3Store()
    asyncio.run(PrototypeStore(FakeS3Store(), root=root, project_id=pid)
                .save_token(slug, token))
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bucket")
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: root)
    return token


def test_a_link_opens_on_a_replaced_instance_through_the_s3_index(env, monkeypatch):
    """When the instance is replaced, `.proto-token` and the cache are empty. Links already handed out
    must resolve via the root index in S3, and that value is planted locally so the cookie check sees the same value."""
    token = _index(monkeypatch)
    app_module.registry.register(PID, "p")
    try:
        _running(env)
        gate = client.get(f"/proto/t/{token}", follow_redirects=False)
        assert gate.status_code == 307
        assert (env["state"] / PID / SLUG / "token").read_text() == token
        assert env["host"].token_for(PID, SLUG) == token
    finally:
        app_module.registry.remove(PID)


def test_an_indexed_token_of_an_unregistered_project_is_404(env, monkeypatch):
    """Tokens for projects being deleted or unknown to this instance are not accepted."""
    token = _index(monkeypatch, pid="not-registered")
    assert client.get(f"/proto/t/{token}", follow_redirects=False).status_code == 404


# ---- Preview surface: prototypes are served only from an origin other than the app (aipds/preview_surface.py) ----

PREVIEW = "https://dpreview.cloudfront.net"


@pytest.fixture()
def preview(monkeypatch):
    monkeypatch.setenv("AIPDS_PREVIEW_ORIGIN", PREVIEW)
    monkeypatch.setenv("AIPDS_PREVIEW_VERIFY", "pv-secret")
    return {"X-Preview-Verify": "pv-secret"}


def test_a_link_opened_on_the_app_domain_moves_to_the_preview_domain(env, preview):
    """Links handed out before the preview surface existed still work — they are sent to the preview domain
    at the same path. No prototype cookie is planted on the app origin."""
    token = env["host"].ensure_token(PID, SLUG)
    _running(env)
    resp = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == f"{PREVIEW}/api/proto/t/{token}"
    assert "set-cookie" not in resp.headers


def test_the_app_domain_does_not_serve_prototypes(env, preview):
    """Even with a correct cookie it is 404 on the app origin — there must be no way for prototype JS
    to run on the same origin as the app cookies."""
    token = env["host"].ensure_token(PID, SLUG)
    _running(env)
    cookies = {cookie_name(PID, SLUG): token}
    assert client.get(f"/proto/{PID}/{SLUG}/", cookies=cookies).status_code == 404
    assert client.get(f"/proto/{PID}/{SLUG}", cookies=cookies,
                      follow_redirects=False).status_code == 404


def test_the_preview_domain_opens_the_link(env, preview):
    token = env["host"].ensure_token(PID, SLUG)
    _running(env)
    gate = client.get(f"/proto/t/{token}", headers=preview, follow_redirects=False)
    assert gate.status_code == 307
    assert gate.headers["location"] == f"/api/proto/{PID}/{SLUG}/"
    assert cookie_name(PID, SLUG) in gate.headers["set-cookie"]


def test_a_wrong_preview_secret_is_the_app_domain(env, preview):
    """Without knowing the secret header value, the app domain cannot be impersonated."""
    token = env["host"].ensure_token(PID, SLUG)
    _running(env)
    resp = client.get(f"/proto/t/{token}", headers={"X-Preview-Verify": "guess"},
                      follow_redirects=False)
    assert resp.headers["location"].startswith(PREVIEW)


def test_share_links_point_at_the_preview_domain(preview):
    from aipds.routes.proto_public import access_url_path
    assert access_url_path("tok") == f"{PREVIEW}/api/proto/t/tok"


def test_without_a_preview_surface_the_app_domain_still_serves(env, monkeypatch):
    """Local development with auth off — there is no session cookie to steal, so the app origin may serve it."""
    monkeypatch.delenv("AIPDS_PREVIEW_ORIGIN", raising=False)
    from aipds.routes.proto_public import access_url_path
    assert access_url_path("tok") == "/api/proto/t/tok"
    token = env["host"].ensure_token(PID, SLUG)
    _running(env)
    assert client.get(f"/proto/t/{token}", follow_redirects=False).status_code == 307


def _auth_on(monkeypatch):
    monkeypatch.setattr(app_module, "cognito_config", lambda: {
        "region": "ap-northeast-2", "user_pool_id": "pool", "client_id": "client"})


def test_with_auth_on_a_missing_preview_surface_closes_the_preview(env, monkeypatch):
    """In a deployment with auth on, if there is no preview surface (missing config, failure reading the secret), the app
    origin does not serve — if it did, prototype JS would call the app API with the session of whoever opened the preview."""
    monkeypatch.delenv("AIPDS_PREVIEW_ORIGIN", raising=False)
    _auth_on(monkeypatch)
    token = env["host"].ensure_token(PID, SLUG)
    _running(env)
    cookies = {cookie_name(PID, SLUG): token}

    gate = client.get(f"/proto/t/{token}", follow_redirects=False)
    assert gate.status_code == 503
    assert "set-cookie" not in gate.headers
    assert client.get(f"/proto/{PID}/{SLUG}/", cookies=cookies).status_code == 404


def test_with_auth_on_an_unreadable_preview_secret_closes_the_preview(env, monkeypatch):
    monkeypatch.setenv("AIPDS_PREVIEW_ORIGIN", PREVIEW)
    monkeypatch.delenv("AIPDS_PREVIEW_VERIFY", raising=False)
    monkeypatch.delenv("AIPDS_PREVIEW_SECRET_ARN", raising=False)
    _auth_on(monkeypatch)
    token = env["host"].ensure_token(PID, SLUG)
    _running(env)
    assert client.get(f"/proto/t/{token}", follow_redirects=False).status_code == 503


# ---- Shared upstream client ----

async def test_the_proxy_reuses_one_upstream_client_per_loop():
    """Creating a client per request means no keep-alive, and the creation cost blocks the loop."""
    from aipds.routes.proto_public import _proxy_http, aclose_proxy_http
    try:
        assert _proxy_http() is _proxy_http()
    finally:
        await aclose_proxy_http()


async def test_the_shared_upstream_client_keeps_no_cookies():
    """The shared client's cookie jar is a leak channel between viewers.

    httpx stores Set-Cookie into the jar via `cookies.extract_cookies` on every response and attaches
    it to later requests. A cookie the prototype planted for one viewer must not ride along on the next
    viewer's request — the browser's cookies are forwarded separately in the Cookie header.
    """
    import httpx
    from aipds.routes.proto_public import _proxy_http, aclose_proxy_http
    try:
        http = _proxy_http()
        upstream = httpx.Response(
            200, headers={"set-cookie": "session=viewer-a; Path=/"},
            request=httpx.Request("GET", "http://127.0.0.1:4001/proto/x/"))
        http.cookies.extract_cookies(upstream)
        assert not http.cookies
    finally:
        await aclose_proxy_http()
