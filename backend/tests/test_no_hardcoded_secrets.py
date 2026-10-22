# backend/tests/test_no_hardcoded_secrets.py — the security review's Bandit password checks, run here first.
#
# The CSR security scan runs Bandit and fails on B105/B106/B107 ("possible hardcoded password"). Bandit
# looks only at names — a variable, argument or dict key with token/secret/password in it, given a
# string literal — so test fixtures (fake tokens, synthetic ARNs) and S3 key prefixes trip it. Every one
# the scan has found was a false positive, but the scan fails all the same, and it runs on a branch long
# after the line was written. Running the same three checks with the suite moves that failure to the
# change that introduced it.
#
# A finding is fixed one of two ways. A real secret leaves the code (Secrets Manager, env). A value that
# is not one keeps its line and gets `# nosec B105 - <why it is not a secret>` (or B106/B107) — the
# convention used throughout (aipds/survey/store.py, tests/test_routes_account.py).
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CHECKS = {"B105", "B106", "B107"}


def test_no_hardcoded_password_findings():
    # In process, through the manager the bandit CLI drives — a subprocess would itself be a finding
    # (the scan's subprocess rule flags a non-literal program, and the venv interpreter is one).
    try:
        from bandit.core import config as bandit_config
        from bandit.core import constants as bandit_constants
        from bandit.core import manager as bandit_manager
    except ImportError:
        pytest.fail("bandit is not installed — install the dev extras (pip install -e '.[dev]')")
    mgr = bandit_manager.BanditManager(
        bandit_config.BanditConfig(), "file", quiet=True,
        profile={"include": set(CHECKS), "exclude": set()})
    # The same targets and default excludes as `bandit -r aipds tests`.
    mgr.discover_files([str(BACKEND / "aipds"), str(BACKEND / "tests")], recursive=True,
                       excluded_paths=",".join(bandit_constants.EXCLUDE))
    mgr.run_tests()
    found = [f"{Path(i.fname).relative_to(BACKEND)}:{i.lineno} {i.test_id} {i.text}"
             for i in mgr.get_issue_list()]
    assert not found, (
        "the security scan fails on these (Bandit B105/B106/B107). A real secret leaves the code; "
        "anything else gets `# nosec <id> - <why it is not a secret>` on that line:\n"
        + "\n".join(found))
