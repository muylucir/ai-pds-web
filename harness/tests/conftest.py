import os
import stat
import textwrap
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
WORKSPACE = "/workspace"


@pytest.fixture(autouse=True)
def _ensure_workspace_dir():
    """`ClaudeDriver.run()` spawns the subprocess with cwd=workspace, and the
    test suite hardcodes WS = "/workspace" (the real MicroVM mount path,
    plan-mandated — kept as-is). Outside the MicroVM image this directory
    doesn't exist by default (e.g. a bare CI runner), which would otherwise
    fail every `run()` test with FileNotFoundError before the test body even
    starts. Create it so `cwd=` is valid; skip cleanly (rather than error) if
    we can't (e.g. read-only root)."""
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
    except PermissionError as exc:
        pytest.skip(f"cannot create {WORKSPACE}: {exc}")


@pytest.fixture
def stub_claude(tmp_path):
    """Write an executable `claude` that ignores its args and prints the named
    jsonl fixture line-by-line, then exits per `exit_code`. Optionally writes
    `stderr_bytes` of filler to stderr first (to exercise stderr-pipe
    draining without deadlock). Returns a builder."""
    def _make(fixture: str = "basic_turn.jsonl", exit_code: int = 0, stderr_bytes: int = 0) -> str:
        payload = (FIXTURES / fixture).read_text() if fixture else ""
        script = tmp_path / "claude"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys
            if {stderr_bytes}:
                sys.stderr.write("E" * {stderr_bytes})
                sys.stderr.flush()
            sys.stdout.write({payload!r})
            sys.exit({exit_code})
        """))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)
    return _make


@pytest.fixture
def hanging_stub_claude(tmp_path):
    """Write an executable `claude` that emits one stream-json line, then
    blocks indefinitely (simulating a long-running turn) instead of exiting.
    Lets a test abandon the driver mid-turn and assert the subprocess is
    actually killed and reaped, not left running."""
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, time
        sys.stdout.write('{"type":"assistant","message":{"content":[{"type":"text","text":"first"}]}}\\n')
        sys.stdout.flush()
        time.sleep(60)
        sys.stdout.write('{"type":"result","subtype":"success"}\\n')
        sys.exit(0)
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)
