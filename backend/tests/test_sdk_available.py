# backend/tests/test_sdk_available.py — the SDK is a NEW backend dependency
# (it used to live only in harness/). These tests fail loudly if the wheel is
# missing or its bundled Claude Code binary can't run on this platform --
# which is exactly the failure that would otherwise surface as an opaque
# "session start failed" 502 at workshop time.
from __future__ import annotations

import subprocess
from pathlib import Path


def test_sdk_imports_with_expected_options():
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # noqa: F401
    from claude_agent_sdk.types import AgentDefinition, HookMatcher  # noqa: F401

    # The four options this feature depends on must exist on the dataclass.
    fields = ClaudeAgentOptions.__dataclass_fields__
    for name in ("session_store", "resume", "setting_sources", "skills"):
        assert name in fields, f"ClaudeAgentOptions lacks {name}"


def test_bundled_binary_is_executable():
    import claude_agent_sdk

    binary = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    assert binary.is_file(), f"bundled binary missing at {binary}"
    proc = subprocess.run([str(binary), "--version"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "Claude Code" in proc.stdout
