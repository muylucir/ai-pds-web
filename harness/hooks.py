# harness/hooks.py  (port 9000 — image build/resume lifecycle)
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from typing import Callable
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

WORKSPACE = "/workspace"


def default_version_check() -> bool:
    exe = shutil.which("claude")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "--version"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def default_rules_present() -> bool:
    core = Path(WORKSPACE) / "aiplc-rules" / "aws-aiplc-rules" / "core-workflow.md"
    return core.is_file()


def build_hooks_app(*, version_check: Callable[[], bool],
                    rules_present: Callable[[], bool],
                    health_check: Callable[[], bool]) -> Starlette:
    async def ready(request):
        # Build-time snapshot gate: 200 only once the app process is up and the
        # Claude Code CLI is installed & runnable. The platform snapshots on 200.
        ok = health_check() and version_check()
        return PlainTextResponse("ready" if ok else "not-ready", status_code=200 if ok else 503)

    async def validate(request):
        # Resume-from-snapshot gate. Tradeoff: a real `claude -p` smoke turn
        # would be the strongest signal but is slow + costs a Bedrock call on
        # EVERY resume, so instead we cheaply re-confirm the server is healthy
        # and the baked rules are present. The platform samples pages to
        # prefetch after 200.
        ok = health_check() and rules_present()
        return PlainTextResponse("valid" if ok else "invalid", status_code=200 if ok else 503)

    return Starlette(routes=[
        Route("/ready", ready, methods=["GET"]),
        Route("/validate", validate, methods=["GET"]),
    ])
