# backend/tests/test_cors.py
from fastapi.testclient import TestClient
from pathfinder.app import app

client = TestClient(app)


def test_preflight_allows_frontend_origin():
    r = client.options(
        "/projects",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_simple_get_echoes_allowed_origin():
    r = client.get("/projects", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_disallowed_origin_is_not_echoed():
    r = client.options(
        "/projects",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-origin") != "http://evil.example"


# Finding 4 (final review): frontend/lib/auth.ts sends `credentials: "include"`
# on every client call so the same-origin /api proxy can read the httpOnly
# session cookie and translate it into Authorization: Bearer. If the backend
# doesn't answer with Access-Control-Allow-Credentials, the browser discards
# every cross-origin response outright -- this is a browser-enforced CORS
# rule, independent of whether auth itself is bypassed.
def test_preflight_allows_credentials():
    r = client.options(
        "/projects",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_simple_request_allows_credentials():
    r = client.get("/projects", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-credentials") == "true"
