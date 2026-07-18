# harness/serve.py  — container CMD: run both servers.
from __future__ import annotations
import asyncio
import httpx
import uvicorn
from app import build_app
from hooks import build_hooks_app, default_version_check, default_rules_present
from claude_driver import ClaudeDriver

WORKSPACE = "/workspace"


def _health_check() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8080/health", timeout=2).is_success
    except httpx.HTTPError:
        return False


async def main() -> None:
    driver = ClaudeDriver(workspace=WORKSPACE)
    app = build_app(driver, WORKSPACE)
    hooks = build_hooks_app(
        version_check=default_version_check,
        rules_present=default_rules_present,
        health_check=_health_check,
    )
    app_server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info"))
    hooks_server = uvicorn.Server(uvicorn.Config(hooks, host="0.0.0.0", port=9000, log_level="info"))
    await asyncio.gather(app_server.serve(), hooks_server.serve())


if __name__ == "__main__":
    asyncio.run(main())
