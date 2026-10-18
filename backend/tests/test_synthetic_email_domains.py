# backend/tests/test_synthetic_email_domains.py — email addresses in the repo use only reserved domains.
#
# This is a public repo. If an example address in tests, comments, or docs uses a domain that can actually be registered (like x.io, b.io,
# company.com), it becomes the address of whoever owns that domain — this is what the CSR scan's Sensitive
# Data rule (SD1) flags as HIGH. Examples use only the reserved names of RFC 2606/6761:
# example.com/.org/.net (and their subdomains), .test, .invalid, .localhost, and .local (mDNS-reserved), which the seed
# accounts use.
#
# Walking `git ls-files` is for the same reason as test_no_legacy_brand.py — what is not tracked goes
# into neither deploys nor reviews.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")

_RESERVED = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net)|[a-z0-9-]+\.(?:test|invalid|localhost|local))$"
    r"|^(?:test|invalid|localhost|local)$",
    re.IGNORECASE)

#: Ones that really are real addresses. The code-of-conduct contact from the aws-samples repo template
#: (CODE_OF_CONDUCT.md, CONTRIBUTING.md) — a public contact, so not personal information.
_PUBLIC_CONTACTS = frozenset({"opensource-codeofconduct@amazon.com"})

#: Registry metadata (package authors, etc.) is not an example we wrote.
_SKIP_SUFFIXES = ("package-lock.json",)


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines()
            if line and not line.endswith(_SKIP_SUFFIXES)]


def test_every_email_address_uses_a_reserved_domain():
    bad = []
    for rel in _tracked():
        path = REPO / rel
        if not path.is_file():
            continue          # submodule gitlink (steering-files), etc.
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in _EMAIL.finditer(line):
                if m.group(0).lower() in _PUBLIC_CONTACTS:
                    continue
                if not _RESERVED.search(m.group(1)):
                    bad.append(f"{rel}:{n}: {m.group(0)}")
    assert not bad, (
        "등록 가능한 도메인의 이메일 주소가 있다 — example.com/.org 같은 예약 도메인으로 "
        "바꿀 것:\n" + "\n".join(bad))
