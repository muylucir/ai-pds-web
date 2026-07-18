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
