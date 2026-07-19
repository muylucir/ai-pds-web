# harness/serve.py  — container CMD: run both servers.
#
# The app server (8080) and the hooks server (9000) run in SEPARATE THREADS,
# each with its own event loop. This is mandatory, not stylistic: the /ready
# hook calls _health_check(), a synchronous blocking httpx.get() to the app
# server on 8080. If both servers shared one event loop (asyncio.gather), that
# blocking call would freeze the loop so 8080 could not answer its own health
# probe — /health times out, health_check() returns False, and /ready 503-loops
# until the build times out (observed on a real deploy: /ready 503 while /health
# 200 in alternation). The Lambda MicroVMs lifecycle doc says the same:
# "Run hooks in a separate thread / event loop from your application server."
from __future__ import annotations
import logging
import os
import threading
import httpx
import uvicorn

# Surface our own diagnostics (harness.driver / harness.hooks) to stdout so
# they reach CloudWatch — e.g. the claude stderr tail on a failed turn.
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
from app import build_app
from hooks import build_hooks_app, default_rules_present, claude_cli_diagnostic, strands_diagnostic

WORKSPACE = "/workspace"


def make_driver(workspace: str = WORKSPACE):
    """PATHFINDER_DRIVER=claude keeps the legacy subprocess driver (rollback);
    default is the Strands in-process agent."""
    if os.environ.get("PATHFINDER_DRIVER") == "claude":
        from claude_driver import ClaudeDriver
        return ClaudeDriver(workspace=workspace)
    from strands_driver import StrandsDriver
    return StrandsDriver(workspace=workspace)


def _health_check() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8080/health", timeout=2).is_success
    except httpx.HTTPError:
        return False


def _serve(app, port: int) -> None:
    uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")).run()


def main() -> None:
    driver = make_driver()
    app = build_app(driver, WORKSPACE)
    hooks = build_hooks_app(
        rules_present=default_rules_present,
        health_check=_health_check,
        cli_diagnostic=claude_cli_diagnostic
            if os.environ.get("PATHFINDER_DRIVER") == "claude" else strands_diagnostic,
    )
    # App server in a daemon thread; hooks server owns the main thread. Two
    # threads => two independent event loops => the hooks' blocking health
    # probe never stalls the app server's loop.
    app_thread = threading.Thread(target=_serve, args=(app, 8080), daemon=True)
    app_thread.start()
    _serve(hooks, 9000)


if __name__ == "__main__":
    main()
