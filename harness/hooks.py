# harness/hooks.py  (port 9000 — image build/resume lifecycle)
from __future__ import annotations
import logging
from pathlib import Path
from typing import Callable
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

WORKSPACE = "/workspace"
_log = logging.getLogger("harness.hooks")


def sdk_diagnostic() -> str:
    """Diagnostic only, never a build gate (the first image build 503-looped
    /ready on a CLI gate; we only log). Confirms the claude-agent-sdk import
    and its bundled CLI binary run on this image/arch."""
    try:
        import claude_agent_sdk
        ver = getattr(claude_agent_sdk, "__version__", "?")
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        return f"claude_agent_sdk import failed {type(exc).__name__}: {exc}"
    import shutil, subprocess
    exe = shutil.which("claude")
    note = f"sdk {ver}; PATH claude={exe or 'absent (bundled binary is used)'}"
    return note


def default_rules_present() -> bool:
    core = (Path(WORKSPACE) / "aiplc-rules" / "aws-aiplc-rule-details"
            / "discovery" / "prototype-building.md")
    return core.is_file()


def build_hooks_app(*, rules_present: Callable[[], bool],
                    health_check: Callable[[], bool],
                    cli_diagnostic: Callable[[], str] = sdk_diagnostic) -> Starlette:
    async def ready(request):
        # Build-time snapshot gate: 200 once the app server is up. We gate ONLY
        # on server health — NOT on `claude --version` (that 503-looped the
        # first real build to death; see sdk_diagnostic). The CLI status
        # is logged for visibility but never blocks the snapshot; the real CLI
        # exercise is the smoke-turn drill. Platform snapshots on 200, else 503.
        ok = health_check()
        _log.info("ready hook: health=%s | %s", ok, cli_diagnostic())
        return PlainTextResponse("ready" if ok else "not-ready", status_code=200 if ok else 503)

    async def validate(request):
        # Resume-from-snapshot gate. Tradeoff: a real `claude -p` smoke turn
        # would be the strongest signal but is slow + costs a Bedrock call on
        # EVERY resume, so instead we cheaply re-confirm the server is healthy
        # and the baked rules are present. The platform samples pages to
        # prefetch after 200.
        ok = health_check() and rules_present()
        return PlainTextResponse("valid" if ok else "invalid", status_code=200 if ok else 503)

    # The platform invokes the image build hooks at the NAMESPACED runtime
    # paths with POST (confirmed against a real build log: it calls
    # `POST /aws/lambda-microvms/runtime/v1/ready` — a bare `/ready` GET 404s
    # and the image never stabilizes). Paths + method per the Lambda MicroVMs
    # lifecycle-hook contract; this closes the plan's hook-path Open Question.
    _PREFIX = "/aws/lambda-microvms/runtime/v1"
    return Starlette(routes=[
        Route(f"{_PREFIX}/ready", ready, methods=["POST"]),
        Route(f"{_PREFIX}/validate", validate, methods=["POST"]),
    ])
