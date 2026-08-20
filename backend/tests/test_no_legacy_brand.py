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

#: **예외는 없다.** 한동안 넷이 있었다 — GitHub 리포 URL, 이미 지운
#: CloudFormation 스택 이름(ko/en 두 줄), S3 미러링 키를 파생하는 uuid5 씨드.
#: 넷 다 "우리 코드 밖의 실체가 옛 이름을 갖고 있다"는 이유였고, 넷 다 그 실체를
#: 옮기거나 지우면서 사라졌다: 스택은 실제로 destroy했고, 씨드는 마이그레이션
#: 절차가 프리픽스를 옮기게 만들어 풀었고, 리포는 `ai-pds-web`으로 개명했다.
#:
#: 그래서 이 단정에 허용 기계가 없다. 다시 필요해지면 그때 넣는다 — 그 결정이
#: 리뷰에 보이는 것이 목록을 미리 비워 두는 것보다 낫고, 쓰이지 않는 예외 통로는
#: 넓혀도 아무 테스트가 깨지지 않는 자리가 된다.
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
    `aipds`라도 이 검사 없이는 그대로 통과한다.
    """
    if not _NAME.search(rel):
        return None
    return f"{rel}: 파일 경로 자체가 옛 제품 이름을 담고 있다"


def _offending_lines(rel: str) -> list[str]:
    path = REPO / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []          # 바이너리·읽을 수 없는 파일은 이 검사의 대상이 아니다
    bad = []
    path_offender = _offending_path(rel)
    if path_offender is not None:
        bad.append(path_offender)
    for number, line in enumerate(text.splitlines(), 1):
        if _NAME.search(line):
            bad.append(f"{rel}:{number}: {line.strip()}")
    return bad


def test_no_tracked_file_mentions_the_old_product_name():
    """제품 이름이 두 겹이면 화면과 로그·문서가 다른 제품을 가리킨다.

    예외 없이 0이다. 이 파일 자신만 뺀다(`_SELF`) — 여기는 금지어를 **말하는**
    자리다.
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


def test_this_guard_has_no_exception_mechanism():
    """예외 통로가 소리 없이 생기는 것을 막는다.

    한동안 허용 목록이 있었다(리포 URL·지운 스택 이름·uuid5 씨드). 넷 다 "우리
    코드 밖의 실체가 옛 이름을 갖고 있다"였고, 넷 다 그 실체를 옮기거나 지우면서
    사라졌다. 지금은 예외가 0이므로 목록도 없다.

    빈 목록을 남겨 두지 않는 이유: 쓰이지 않는 예외 통로는 넓혀도 아무 테스트가
    깨지지 않는 자리가 된다. 다시 필요해지면 기계를 되살리는 커밋이 그 결정을
    리뷰에 드러낸다 — 이 테스트가 먼저 깨져서 그렇게 만든다.
    """
    assert not hasattr(_this_module(), "ALLOWED"), (
        "허용 기계를 되살렸다면 왜 필요한지, 왜 그 범위인지를 이 테스트와 함께 "
        "고쳐라 — 목록의 존재 자체가 리뷰에 보여야 한다")


def _this_module():
    import sys
    return sys.modules[__name__]


def test_the_session_id_seed_derives_the_migrated_uuid():
    """uuid5 씨드가 마이그레이션이 옮겨 놓는 S3 프리픽스와 맞는 UUID를 내야 한다.

    **이 값은 자유롭게 고칠 수 없다.** 씨드는 이름이 아니라 해시 입력이고,
    `session_store._session_prefix`가 그 출력을 S3 키로 쓴다 — 씨드를 바꾸면
    기존 트랜스크립트가 조용히 도달 불가가 되고 모든 프로젝트의 Discovery
    대화가 빈 세션으로 시작한다. 이번 개명에서 씨드를 `aipds:`로 바꾼 대가로
    운영 매뉴얼의 마이그레이션 절차가 프리픽스를 옮기는 단계를 갖는다. 다음에
    이 값을 건드리는 사람도 같은 이전을 해야 하고, 이 테스트가 먼저 깨져서
    그것을 알게 된다.

    실측 대응(개명 전 → 후):

        ship       e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3 -> 95822f5b-0507-5622-b85e-669f7da9bbe5
        test1111   7565ab2e-fc2a-5909-9df8-d4dcb5b66661 -> 7a2934e1-45dd-5cf1-9576-fa1c160b8282
        test2222   bbeda823-6b7e-5612-90cf-f706829a6c41 -> a4650830-9720-554b-83d2-6b1ab127307b

    왼쪽이 배포된 버킷에 실제로 있던 프리픽스이고, 오른쪽이 이전 후의 값이다.
    `resume`은 `session`에 그 키가 없으므로 `bool(None)` = `False`가 맞다
    (`_sdk_session_id`의 두 번째 줄).
    """
    from aipds.agent.claude_driver import _sdk_session_id

    assert _sdk_session_id({"session_id": "ship"}) == (
        "95822f5b-0507-5622-b85e-669f7da9bbe5", False)


def test_the_session_prefix_still_lands_on_the_measured_s3_path():
    """씨드가 맞아도, 그 씨드를 담는 키 모양이 바뀌면 실측 데이터는 여전히 조용히 사라진다.

    위 테스트는 씨드(uuid5의 출력값)만 고정한다 — `_session_prefix`가 그 씨드를
    어디에 두는지는 보지 않는다. `_session_prefix`를 다시 짜는 사람은 씨드
    테스트를 건드리지 않고도 통과시킬 수 있으므로, 이 테스트가 없으면 모든
    프로젝트의 discovery 히스토리가 조용히 사라져도 아무 테스트도 잡지 못한다.

    실측: 배포된 버킷의 경로는 개명 전
    `projects/ship/discovery/transcript/e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3/main/`
    이었고, 마이그레이션 절차가 이것을 아래 씨드 아래로 옮긴다. 그
    `projects/ship/` 부분은 이 모듈 밖(`app.s3_store_factory`가
    `projects/{project_id}/`로 스코프한 스토어를 `app.driver_factory`가
    `ClaudeDriver`에 넘기고, `ClaudeDriver`가 그 스토어로 `DiscoverySessionStore`를
    만든다)에서 붙으므로, 여기서는 `_session_prefix` 자신이 반환하는 나머지
    (`discovery/transcript/<uuid>/main/`)만 이어붙여 맞는지 본다.
    """
    from aipds.agent.session_store import _session_prefix

    seed = "95822f5b-0507-5622-b85e-669f7da9bbe5"
    prefix = _session_prefix({"session_id": seed})
    assert prefix == f"discovery/transcript/{seed}/main/"
    # 프로젝트 스코프("projects/ship/")를 이어붙이면 이전 후 경로와 정확히 같아야 한다.
    assert f"projects/ship/{prefix}" == (
        "projects/ship/discovery/transcript/"
        "95822f5b-0507-5622-b85e-669f7da9bbe5/main/")
