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

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CHECKS = "B105,B106,B107"


def test_no_hardcoded_password_findings():
    proc = subprocess.run(
        [sys.executable, "-m", "bandit", "-q", "-r", "aipds", "tests", "-t", CHECKS, "-f", "json"],
        cwd=BACKEND, capture_output=True, text=True, timeout=120)
    # Exit 1 also means "findings", so tell "did not run" apart by the missing report.
    assert proc.stdout.strip(), (
        "bandit did not run — install the dev extras (pip install -e '.[dev]'):\n"
        + proc.stderr[-2000:])
    found = [f"{r['filename']}:{r['line_number']} {r['test_id']} {r['issue_text']}"
             for r in json.loads(proc.stdout)["results"]]
    assert not found, (
        "the security scan fails on these (Bandit " + CHECKS + "). A real secret leaves the code; "
        "anything else gets `# nosec <id> - <why it is not a secret>` on that line:\n"
        + "\n".join(found))
