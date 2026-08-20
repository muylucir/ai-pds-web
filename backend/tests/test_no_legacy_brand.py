# backend/tests/test_no_legacy_brand.py — 제품 이름은 하나여야 한다.
#
# `git ls-files`를 훑는다. 추적되지 않는 것(docs/ 아래 SDD 산출물, node_modules,
# .venv)은 배포에도 리뷰에도 들어가지 않으므로 검사 대상이 아니다.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: 세 허용 모두 **우리 코드 밖의 진짜 이름**을 가리키는 값이다 — 우리 이름과
#: 우리 코드는 전부 바꾼다; 밖의 것을 가리키는 참조는 밖의 실제 값과 맞아야
#: 하므로 바꾸지 않는다. 그래서 이 파일이 아니라 여기 이 값 자체를 넓히는
#: 것은 금지다(아래 두 테스트가 목록의 크기와 각 줄의 실재를 함께 고정한다).
ALLOWED = {
    # GitHub 리포 이름. 리포는 이 계획의 범위 밖이고 이름을 유지하는 것이
    # 결정 사항이다 — 이 값이 바뀌면 부팅의 `git clone`이 깨진다.
    "infra/lib/deploy-source.ts": re.compile(r"ai-plc-pathfinder"),
    # `PathfinderVmStack`은 이 리네임 계획이 시작되기 전에 코드에서 이미
    # 삭제된 CloudFormation 스택이다. 그 시절 배포한 인스턴스가 있다면 그
    # 스택은 지금도 AWS 안에 그 리터럴 이름으로 남아 있다 — 이 절은 그것을
    # 지우는 방법을 알려 주는 운영 안내문이므로, `AipdsVmStack`으로 고치면
    # 존재하지 않는 스택을 가리키는 틀린 명령이 된다. 파일 전체가 아니라 이
    # 문자열 하나만 허용한다 — 같은 파일의 다른 회귀는 여전히 잡혀야 한다.
    "infra/README.md": re.compile(r"PathfinderVmStack"),
    "infra/README.ko.md": re.compile(r"PathfinderVmStack"),
    # session_store.py가 트랜스크립트를 S3에 미러링하는 키의 구성요소가 이
    # uuid5 네임스페이스 씨드다 — 이름이 아니라 **해시 입력**이다. 이미 그
    # 값으로 파생된 UUID 아래 데이터가 실존하므로(실측: project_id "ship" →
    # `e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3`, 그 프리픽스 아래 실제
    # `projects/ship/discovery/transcript/...jsonl`가 있다), 이 문자열을
    # 바꾸면 씨드가 바뀌어 기존 세션 전부가 조용히 새 대화로 시작된다 — 마이
    # 그레이션 절차가 지키기로 한 "명세·설문·응답은 살아 있다"를 정확히
    # 어긴다. 아래 test_the_session_id_seed_derives_the_measured_uuid가 이
    # 값이 실제로 그 UUID로 이어짐을 고정한다.
    "backend/aipds/agent/claude_driver.py": re.compile(r'pathfinder:\{raw\}'),
}

_NAME = re.compile(r"pathfinder", re.IGNORECASE)

#: 이 파일 자신의 상대 경로. 여기는 금지어를 **말하는** 자리다 — 허용 목록의
#: 값, 그 이유를 적은 주석, 이 상수 이름 자체가 전부 "pathfinder"를 담는다.
#: 그것은 옛 이름이 야생에 남은 것이 아니라 이 단정이 무엇을 막는지 설명하는
#: 것이므로 스캔 대상에서 뺀다 — node_modules·.venv를 빼는 것과 같은 이유다.
_SELF = Path(__file__).resolve().relative_to(REPO).as_posix()


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line and line != _SELF]


def _offending_lines(rel: str) -> list[str]:
    path = REPO / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []          # 바이너리·읽을 수 없는 파일은 이 검사의 대상이 아니다
    allowed = ALLOWED.get(rel)
    bad = []
    for number, line in enumerate(text.splitlines(), 1):
        if not _NAME.search(line):
            continue
        if allowed is not None and allowed.search(line):
            continue
        bad.append(f"{rel}:{number}: {line.strip()}")
    return bad


def test_no_tracked_file_mentions_the_old_product_name():
    """제품 이름이 두 겹이면 화면과 로그·문서가 다른 제품을 가리킨다.

    허용은 넷뿐이다 — GitHub 리포 URL, 이미 삭제된 CloudFormation 스택 이름
    (ko/en 두 줄), S3 미러링 키를 파생하는 uuid5 씨드. 전부 우리 코드 밖의
    진짜 값을 가리키므로 바꾸지 않는다(ALLOWED 정의의 주석 참고).
    """
    offenders = [line for rel in _tracked() for line in _offending_lines(rel)]
    assert not offenders, (
        f"옛 제품 이름이 {len(offenders)}곳에 남아 있다:\n"
        + "\n".join(offenders[:40]))


def test_the_allowances_are_exactly_these_four_lines():
    """허용 목록이 소리 없이 늘어나는 것을 막는다.

    새 허용을 넣으려면 이 테스트도 고쳐야 하므로, 그 결정이 리뷰에 보인다.
    """
    assert set(ALLOWED) == {
        "infra/lib/deploy-source.ts",
        "infra/README.md",
        "infra/README.ko.md",
        "backend/aipds/agent/claude_driver.py",
    }


def test_every_allowance_still_points_at_a_real_line():
    """허용 목록이 유령을 가리키지 않는다 — 가리키는 값이 실제로 바뀌면 실패한다."""
    for rel, pattern in ALLOWED.items():
        text = (REPO / rel).read_text(encoding="utf-8")
        assert pattern.search(text), f"{rel}의 허용 패턴이 더는 실재를 가리키지 않는다"


def test_the_session_id_seed_derives_the_measured_uuid():
    """uuid5 씨드가 실제 S3 프리픽스와 맞는 UUID를 계속 내야 한다.

    이 테스트가 이 허용의 진짜 근거다 — 주석은 무시할 수 있어도 실패하는
    테스트는 무시할 수 없다. 씨드를 고치는 사람은 이 테스트가 먼저 깨져서
    무엇을 건드렸는지 보게 된다.

    실측: 배포된 버킷의 `projects/ship/discovery/transcript/
    e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3/main/00000001.jsonl`가 이 값으로
    이미 존재한다. `resume`은 `session`에 그 키가 없으므로 `bool(None)` =
    `False`가 맞다(`_sdk_session_id`의 두 번째 줄).
    """
    from aipds.agent.claude_driver import _sdk_session_id

    assert _sdk_session_id({"session_id": "ship"}) == (
        "e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3", False)
