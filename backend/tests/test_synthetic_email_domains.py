# backend/tests/test_synthetic_email_domains.py — 리포의 이메일 주소는 예약 도메인만 쓴다.
#
# 공개 리포다. 테스트·주석·문서의 예시 주소가 실제로 등록 가능한 도메인(x.io, b.io,
# company.com 같은)이면, 그 도메인을 가진 누군가의 주소가 된다 — CSR 스캔의 Sensitive
# Data 룰(SD1)이 HIGH로 보는 것이 이것이다. 예시는 RFC 2606/6761의 예약 이름만 쓴다:
# example.com/.org/.net(과 그 하위 도메인), .test, .invalid, .localhost, 그리고 시드
# 계정이 쓰는 .local(mDNS 예약).
#
# `git ls-files`를 훑는 것은 test_no_legacy_brand.py와 같은 이유다 — 추적되지 않는 것은
# 배포에도 리뷰에도 들어가지 않는다.
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

#: 실제 주소가 맞는 것. aws-samples 리포 템플릿의 행동 강령 연락처다
#: (CODE_OF_CONDUCT.md, CONTRIBUTING.md) — 공개 연락처라 개인 정보가 아니다.
_PUBLIC_CONTACTS = frozenset({"opensource-codeofconduct@amazon.com"})

#: 레지스트리 메타데이터(패키지 저자 등)는 우리가 쓴 예시가 아니다.
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
            continue          # 서브모듈 gitlink(steering-files) 등
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
