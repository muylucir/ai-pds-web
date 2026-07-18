import os
import stat
import textwrap
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def stub_claude(tmp_path):
    """Write an executable `claude` that ignores its args and prints the named
    jsonl fixture line-by-line, then exits per `exit_code`. Returns a builder."""
    def _make(fixture: str = "basic_turn.jsonl", exit_code: int = 0) -> str:
        payload = (FIXTURES / fixture).read_text() if fixture else ""
        script = tmp_path / "claude"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys
            sys.stdout.write({payload!r})
            sys.exit({exit_code})
        """))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)
    return _make
