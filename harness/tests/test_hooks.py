import httpx
from hooks import build_hooks_app

# The platform invokes build hooks at the namespaced runtime paths with POST
# (confirmed against a real build log — a bare GET /ready 404s and the image
# never stabilizes).
READY = "/aws/lambda-microvms/runtime/v1/ready"
VALIDATE = "/aws/lambda-microvms/runtime/v1/validate"


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vm")


async def test_ready_200_when_healthy():
    # /ready gates ONLY on server health — the CLI diagnostic never blocks it
    # (gating on `claude --version` 503-looped the first real build to death).
    calls = []
    app = build_hooks_app(rules_present=lambda: True, health_check=lambda: True,
                          cli_diagnostic=lambda: (calls.append(1), "claude ok")[1])
    async with _client(app) as http:
        assert (await http.post(READY)).status_code == 200
    assert calls, "cli diagnostic should be invoked (logged) even though it doesn't gate"


async def test_ready_200_even_when_cli_diagnostic_reports_broken():
    # The build must NOT fail just because the CLI check is unhappy — this is
    # the exact regression that broke the first real deploy.
    app = build_hooks_app(rules_present=lambda: True, health_check=lambda: True,
                          cli_diagnostic=lambda: "claude: not on PATH")
    async with _client(app) as http:
        assert (await http.post(READY)).status_code == 200


async def test_ready_503_when_server_unhealthy():
    app = build_hooks_app(rules_present=lambda: True, health_check=lambda: False,
                          cli_diagnostic=lambda: "n/a")
    async with _client(app) as http:
        assert (await http.post(READY)).status_code == 503


async def test_validate_200_when_health_and_rules_ok():
    app = build_hooks_app(rules_present=lambda: True, health_check=lambda: True,
                          cli_diagnostic=lambda: "n/a")
    async with _client(app) as http:
        assert (await http.post(VALIDATE)).status_code == 200


async def test_validate_503_when_rules_missing():
    app = build_hooks_app(rules_present=lambda: False, health_check=lambda: True,
                          cli_diagnostic=lambda: "n/a")
    async with _client(app) as http:
        assert (await http.post(VALIDATE)).status_code == 503


def test_strands_diagnostic_never_raises():
    from hooks import strands_diagnostic
    assert isinstance(strands_diagnostic(), str)


async def test_bare_paths_404_platform_uses_namespaced():
    # Guard the exact bug that broke the first real build: the platform never
    # calls bare /ready or GET; those must not resolve.
    app = build_hooks_app(rules_present=lambda: True, health_check=lambda: True,
                          cli_diagnostic=lambda: "n/a")
    async with _client(app) as http:
        assert (await http.post("/ready")).status_code == 404
        assert (await http.get(READY)).status_code == 405
