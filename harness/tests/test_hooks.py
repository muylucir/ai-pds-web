import httpx
from hooks import build_hooks_app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vm")


async def test_ready_200_when_version_ok():
    app = build_hooks_app(version_check=lambda: True,
                          rules_present=lambda: True, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/ready")).status_code == 200


async def test_ready_503_when_version_fails():
    app = build_hooks_app(version_check=lambda: False,
                          rules_present=lambda: True, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/ready")).status_code == 503


async def test_validate_200_when_health_and_rules_ok():
    app = build_hooks_app(version_check=lambda: True,
                          rules_present=lambda: True, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/validate")).status_code == 200


async def test_validate_503_when_rules_missing():
    app = build_hooks_app(version_check=lambda: True,
                          rules_present=lambda: False, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/validate")).status_code == 503
