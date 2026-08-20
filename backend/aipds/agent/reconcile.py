# backend/aipds/agent/reconcile.py — 워크스페이스에서 UI 이벤트를 **유도**한다.
#
# **왜 이 모듈이 생겼는가.** 스테이지 사이드바와 Prototypes 카드는 원래 모델이
# 부르는 MCP 도구(`report_stage`, `handoff_prototype`)가 만들었다. 도구는 모델이
# 부르지 않으면 아무 일도 일어나지 않고, 그 침묵이 두 번 실측됐다:
#
#   - 2026-08-18 test123456: PostToolUse 훅이 질문 파일 쓰기에서 턴을 끝내자 같은
#     메시지에 배치된 `report_stage`가 실행되지 않았다. `aiplc-state.md`가 없어
#     배지가 프로젝트 내내 비었고, 재개 턴은 "멈춘 지점부터"라 회수 계기가 없었다.
#   - 2026-08-17 keumkang-v5: `handoff_prototype`이 없던 시절 에이전트가 자격증명을
#     묻고 선행 조건을 나열했다. 탭 안내는 0회였다.
#
# 그래서 판정 기준을 "모델이 선언했는가"에서 **"디스크가 무엇을 말하는가"**로
# 옮긴다. 파일은 유실되지 않는다 — 배치 드롭도, 턴 종료도, 모델의 건망증도 파일을
# 지우지 못한다.
#
# **여기 있는 것과 없는 것.** 파일에서 **파싱**되는 신호만 여기서 유도한다.
# `submit_document`의 `version`과 "리뷰 준비됨 vs 중간 저장"은 파싱이 아니라
# 판단이므로 도구로 남아 있고, `build_complete`도 그렇다(빌드의 마지막 Write는
# 다른 Write와 구별되지 않는다 — proto/tools.py:6-7).
#
# **왜 diff인가.** agent/tools.py의 옛 헤더가 "상태 파일 쓰기에서 역추론하면 한 턴에
# 여러 번 갱신될 때 UI가 흔들린다"고 걱정했다. 그 걱정은 정당했지만 해법은 도구가
# 아니라 diff다: 프론트가 `stage` 이벤트를 **누적**하므로(useWorkspaceStream.ts:189
# `[...prev, parsed]`) 같은 상태를 다시 흘리면 목록이 자란다. 상태가 실제로 바뀐
# 스테이지만 흘리면 그 문제가 사라지고, 옛 도구보다 오히려 조용하다 — 도구는 모델이
# 같은 스테이지를 두 번 선언하면 두 번 쐈다.
#
# **순수 함수 + 명시적 커서.** 드라이버가 커서(`last`, `announced`)를 들고 있고 이
# 모듈은 그것을 받아 새 커서를 돌려준다. 모듈 전역 상태를 두지 않는 이유는 드라이버가
# 프로젝트당 하나이고 테스트가 커서를 직접 넣어 diff 경로를 검사해야 하기 때문이다.
from __future__ import annotations

import json
import logging
from pathlib import Path

from aipds.models import AgentEvent
from aipds.parsers.state import parse_state_file
from aipds.proto import layout

_log = logging.getLogger("aipds.agent")

#: 상류 룰이 정한 상태 파일. `core-workflow.md`의 트리와
#: `prototype-validation.md` Step 10이 이 경로를 쓴다.
STATE_KEY = "aiplc-docs/aiplc-state.md"

#: Step 3의 마지막 산출물 이름. `prototype-validation.md:170`이 단수 경로로
#: 선언하고(`aiplc-docs/discovery/prototype/build-instructions.md`), Path B에서는
#: 같은 이름이 슬러그 디렉터리 아래 온다 — 경로 조립은 `layout.artifact_dir`이
#: 단독으로 소유하므로 여기서는 **파일 이름만** 안다.
BUILD_INSTRUCTIONS = "build-instructions.md"


def stage_events(markdown: str | None,
                 last: dict[str, str]) -> tuple[list[AgentEvent], dict[str, str]]:
    """`aiplc-state.md` 전문 → 상태가 **바뀐** 스테이지의 `stage` 이벤트.

    `last`는 "이 스테이지를 이 상태로 이미 흘렸다"는 커서다. 돌려주는 dict가 새
    커서이고, 호출부가 그것으로 교체한다.

    파일 순서를 유지한다. 체크리스트 순서가 곧 방법론의 스테이지 순서이므로
    (`core-workflow.md`의 Stage Progress), 정렬하면 사이드바가 룰과 다른 순서로
    쌓인다.

    **`current_stage`를 별도로 만들지 않는다.** `parse_state_file`이 이미 그것을
    체크리스트의 한 줄에 `in_progress`로 접어 넣는다(정확 일치 우선, 없으면 최장
    부분 일치). 그 접기 밖에 있는 `current_stage`는 체크리스트에 줄이 없는
    이름이라는 뜻이고, 그때 항목을 하나 만들어 내면 룰이 정하지 않은 스테이지가
    사이드바에 생긴다 — 파서가 이미 판단한 것을 여기서 뒤집지 않는다.

    빈 파일/None은 이벤트 없음이다. 그것은 "스테이지가 없다"는 사실이고, 없다는
    사실로 화면을 지우지는 않는다(에이전트가 파일을 잠깐 비웠다가 다시 쓰는 중일
    수 있다).
    """
    if not markdown or not markdown.strip():
        return [], last
    try:
        state = parse_state_file(markdown)
    except Exception:
        # 손상된 상태 파일로 턴을 죽이지 않는다 — 배지가 낡는 것이 턴 실패보다 낫고,
        # 다음 쓰기가 다시 기회를 준다.
        _log.exception("could not parse %s — leaving the stage badges as they are",
                       STATE_KEY)
        return [], last
    events: list[AgentEvent] = []
    cursor = dict(last)
    for stage in state.stages:
        if cursor.get(stage.name) == stage.status:
            continue
        cursor[stage.name] = stage.status
        events.append(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage.name, "status": stage.status,
             "summary": stage.note or ""}, ensure_ascii=False)))
    return events, cursor


def _discovery_keys(workspace: Path) -> list[str]:
    """`aiplc-docs/discovery/` 아래 파일을 워크스페이스 상대 POSIX 경로로 걷는다.

    `layout.discover`가 이 모양을 받도록 설계돼 있어(proto/layout.py) 목록
    라우트와 **같은 함수**로 판정할 수 있다. S3가 아니라 로컬을 읽는 이유는
    에이전트가 방금 Write한 파일을 곧바로 판정해야 하기 때문이다 — 그 시점의
    정본은 디스크다(옛 `tools._discovery_keys`가 같은 판단을 기록해 뒀다).
    """
    base = workspace / layout.DISCOVERY_PREFIX
    if not base.is_dir():
        return []
    return sorted(p.relative_to(workspace).as_posix()
                  for p in base.rglob("*") if p.is_file())


def prototype_id_for(rel: str) -> str | None:
    """이 경로가 어떤 프로토타입의 `build-instructions.md`라면 그 id, 아니면 None.

    `layout.artifact_dir`의 역이다. 경로를 직접 파싱하지 않고 **후보 id로 조립해
    비교**한다 — 레이아웃 규약은 그 모듈이 단독 소유하고, 여기서 정규식을 한 벌 더
    쓰면 규칙이 두 곳에 있게 된다(layout.py 헤더의 "네 곳에 복제" 비용이 그것이다).

    후보는 경로에서 온다: 마지막 디렉터리 이름이 id 후보다(단수는 `prototype`,
    슬러그는 `{slug}`). 그것을 `artifact_dir`에 넣어 원래 경로가 나오면 맞다.
    """
    p = Path(rel)
    if p.name != BUILD_INSTRUCTIONS:
        return None
    candidate = p.parent.name
    if not candidate:
        return None
    if f"{layout.artifact_dir(candidate)}/{BUILD_INSTRUCTIONS}" != rel:
        return None
    return candidate


def handed_off(workspace: Path) -> dict[str, str]:
    """`build-instructions.md`가 **디스크에 있는** 프로토타입 → 그 명세 경로.

    명세와 빌드 지시 **둘 다** 있어야 인계로 본다. 명세가 없으면 Prototypes 탭이
    카드를 만들지 못하므로(routes/prototypes.py가 `layout.discover`로 목록을 만든다)
    사용자가 빈 탭을 본다 — 옛 `handoff_prototype`이 명세 존재를 확인한 이유가
    그것이고, 그 검사가 여기로 옮겨 온 것이다.
    """
    keys = _discovery_keys(workspace)
    specs = layout.discover(keys)
    present = {k for k in keys if prototype_id_for(k) is not None}
    return {pid: spec for pid, spec in specs.items()
            if f"{layout.artifact_dir(pid)}/{BUILD_INSTRUCTIONS}" in present}


def prototype_events(workspace: Path,
                     announced: set[str]) -> tuple[list[AgentEvent], set[str]]:
    """아직 알리지 않은 인계를 `prototype_ready` 이벤트로.

    `announced`가 커서다 — 같은 프로토타입을 두 번 알리면 채팅에 카드가 두 장 뜬다.
    """
    events: list[AgentEvent] = []
    cursor = set(announced)
    for pid, spec in sorted(handed_off(workspace).items()):
        if pid in cursor:
            continue
        cursor.add(pid)
        events.append(AgentEvent(kind="prototype_ready", payload=json.dumps(
            {"slug": pid, "spec_path": spec}, ensure_ascii=False)))
    return events, cursor


def read_state(workspace: Path) -> str | None:
    """워크스페이스의 상태 파일 전문. 없으면 None.

    읽기 실패도 None이다 — 재조정은 백스톱이고, 그것이 턴을 실패시키면 백스톱이
    아니라 새 실패 원인이 된다.
    """
    try:
        p = workspace / STATE_KEY
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except OSError:
        _log.exception("could not read %s", STATE_KEY)
        return None
