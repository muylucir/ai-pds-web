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


def _offending_path(rel: str) -> str | None:
    """`rel` 자신이 옛 이름을 담고 있으면 그 경로를 단정한다.

    이 브랜치의 대부분은 **파일 이름 바꾸기**(83개)였다 — 내용을 훑는
    `_offending_lines`는 그 카테고리를 전혀 보지 않는다: 되살아난
    `infra/scripts/pathfinder-update`나 `backend/pathfinder/`가 내용은 전부
    `aipds`라도 이 검사 없이는 그대로 통과한다. 허용은 경로 자체에도 적용한다
    — 네 허용 모두 경로 자체는 깨끗하므로(`aipds`만 담고 있다), 이 검사가
    새 허용을 요구하지는 않는다.
    """
    if not _NAME.search(rel):
        return None
    allowed = ALLOWED.get(rel)
    if allowed is not None and allowed.search(rel):
        return None
    return f"{rel}: 파일 경로 자체가 옛 제품 이름을 담고 있다"


def _offending_lines(rel: str) -> list[str]:
    path = REPO / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []          # 바이너리·읽을 수 없는 파일은 이 검사의 대상이 아니다
    allowed = ALLOWED.get(rel)
    bad = []
    path_offender = _offending_path(rel)
    if path_offender is not None:
        bad.append(path_offender)
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


def test_the_path_check_flags_a_resurrected_old_name_path():
    """`_offending_path`가 파일 **내용**이 아니라 **경로 자체**를 본다.

    이 브랜치의 대부분(83개)은 파일 이름 바꾸기였다 — 내용을 훑는
    `_offending_lines`의 줄 단위 루프는 그 카테고리를 전혀 보지 못한다. 실제
    파일을 만들지 않고 이 헬퍼를 직접 불러 확인한다: 이렇게 해야 이 검사를
    지워도(즉 `_offending_path`를 항상 `None`으로 되돌려도) 이 테스트 하나가
    바로 깨진다 — 리포에 그 경로의 파일이 실재해야 하는 회귀 테스트보다
    이쪽이 이 검사의 존재 자체를 더 직접적으로 고정한다.
    """
    assert _offending_path("infra/scripts/pathfinder-update") is not None
    assert _offending_path("backend/pathfinder/app.py") is not None
    # 대조: 이름이 깨끗한 경로는 걸리지 않는다.
    assert _offending_path("infra/scripts/aipds-update") is None
    # 대조: 허용 목록에 있는 경로는 (경로 자체가 깨끗하므로) 걸리지 않는다.
    for rel in ALLOWED:
        assert _offending_path(rel) is None


def test_the_allowances_are_exactly_these_four_lines():
    """허용 목록이 소리 없이 늘어나는 것을 막는다.

    새 허용을 넣으려면 이 테스트도 고쳐야 하므로, 그 결정이 리뷰에 보인다.

    경로만 비교하면 통과하지 못한다 — 패턴까지 값으로 고정한다. ALLOWED의
    주석은 패턴을 넓히지 말라고 말하지만, 집합 비교는 경로만 보므로
    "infra/README.md"의 패턴을 `PathfinderVmStack`에서 `Pathfinder`로,
    또는 driver의 패턴을 맨 `pathfinder`로 넓혀도 이 테스트는 여전히
    통과했다 — 그렇게 넓힌 순간 두 파일의 나머지 회귀는 이 가드가 더 이상
    잡지 못한다. 패턴 문자열까지 비교하면 그 넓히기 자체가 여기서 실패한다.
    """
    assert {rel: p.pattern for rel, p in ALLOWED.items()} == {
        "infra/lib/deploy-source.ts": r"ai-plc-pathfinder",
        "infra/README.md": r"PathfinderVmStack",
        "infra/README.ko.md": r"PathfinderVmStack",
        "backend/aipds/agent/claude_driver.py": r"pathfinder:\{raw\}",
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


def test_the_session_prefix_still_lands_on_the_measured_s3_path():
    """씨드가 맞아도, 그 씨드를 담는 키 모양이 바뀌면 실측 데이터는 여전히 조용히 사라진다.

    위 테스트는 씨드(uuid5의 출력값)만 고정한다 — `_session_prefix`가 그 씨드를
    어디에 두는지는 보지 않는다. `_session_prefix`를 다시 짜는 사람은 씨드
    테스트를 건드리지 않고도 통과시킬 수 있으므로, 이 테스트가 없으면 모든
    프로젝트의 discovery 히스토리가 조용히 사라져도 아무 테스트도 잡지 못한다.

    실측(ALLOWED 정의의 주석과 같은 실측): 배포된 버킷의 실제 경로는
    `projects/ship/discovery/transcript/e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3/main/`
    이다. 그 `projects/ship/` 부분은 이 모듈 밖(`app.s3_store_factory`가
    `projects/{project_id}/`로 스코프한 스토어를 `app.driver_factory`가
    `ClaudeDriver`에 넘기고, `ClaudeDriver`가 그 스토어로 `DiscoverySessionStore`를
    만든다)에서 붙으므로, 여기서는 `_session_prefix` 자신이 반환하는 나머지
    (`discovery/transcript/<uuid>/main/`)만 그 실측값과 이어붙여 맞는지 본다.
    """
    from aipds.agent.session_store import _session_prefix

    seed = "e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3"
    prefix = _session_prefix({"session_id": seed})
    assert prefix == f"discovery/transcript/{seed}/main/"
    # 프로젝트 스코프("projects/ship/")를 이어붙이면 실측 경로와 정확히 같아야 한다.
    assert f"projects/ship/{prefix}" == (
        "projects/ship/discovery/transcript/"
        "e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3/main/")
