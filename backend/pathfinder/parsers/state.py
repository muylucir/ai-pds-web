# backend/pathfinder/parsers/state.py
from __future__ import annotations
import re
from pathfinder.models import ProjectState, StageState

_PROJECT_TYPE = re.compile(r"\*\*Project Type\*\*:\s*(.+)")
_CURRENT_STAGE = re.compile(r"\*\*Current Stage\*\*:\s*(.+)")
_CHECK = re.compile(r"^- \[([ xX])\]\s*(.+)$")
_SPLIT = re.compile(r"\s+[—-]\s+")

#: 스테이지 이름에 실제로 나타난 이스케이프. XML 사전정의 엔티티 5개로 좁혔다.
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">",
             "&quot;": '"', "&#39;": "'", "&apos;": "'"}
_ENTITY_RE = re.compile("|".join(re.escape(k) for k in _ENTITIES))


def normalize_stage_name(raw: str) -> str:
    """스테이지 이름의 HTML 엔티티를 되돌리고 앞뒤 공백을 다듬는다.

    **왜 필요한가(2026-08-18 실측: hpt-sarang).** 모델이 `report_stage`에
    `"stage": "Prototype &amp; Validation"`을 보냈다. 같은 호출의 `summary`와
    직전 `Solution Analysis` 호출은 `&`가 정상이었으니, 그 필드 하나에서만
    일어난 이스케이프다 — 룰셋과 우리 코드에는 `&amp;`가 한 곳도 없다(전수 grep).

    스테이지 이름은 표시 문자열이 아니라 **키**다. 손대지 않으면 셋이 함께
    깨진다:

    1. 사이드바에 `Prototype &amp; Validation`이 그대로 뜬다.
    2. 아래 `parse_state_file`의 이름 매칭이 어긋난다 — `&amp;`가 박힌 줄은
       다음의 **올바른** 줄과 이름이 다르므로 체크라인이 하나 더 생기고,
       진행률이 같은 스테이지를 두 번 센다.
    3. 부분 포함 폴백(`_names_match`)이 엉뚱한 줄을 최장 일치로 고를 수 있다.

    **읽기 쪽에서 정규화하는 것이 요점이다.** 2026-08-18까지는 쓰기 쪽
    (`report_stage` 도구)도 이것을 불렀는데, 그 도구는 훅으로 대체됐고 지금
    `aiplc-state.md`를 쓰는 것은 에이전트다 — 즉 우리가 손댈 쓰기 경로가 아예
    없다. 이미 `&amp;`가 박혀 저장된 파일도 읽기 쪽 정규화가 파일을 고치지 않고
    치유한다(hpt-sarang의 상태 파일이 그 경우다).

    `html.unescape`를 쓰지 않는 이유: 그쪽은 세미콜론 없는 `&notin` 같은 형태와
    수백 개의 명명 엔티티까지 관대하게 되돌리므로, `&copy`가 들어간 이름을
    조용히 `©`로 바꾼다. 스테이지 이름에 필요한 것은 위 다섯 개뿐이고, 좁은
    치환은 예상 못 한 이름 변형을 만들지 않는다. 한 번의 `re.sub`로 처리하므로
    치환 순서에 의한 `&amp;lt;` → `<` 같은 사고도 없다.
    """
    return _ENTITY_RE.sub(lambda m: _ENTITIES[m.group(0)], raw).strip()

def parse_state_file(markdown: str) -> ProjectState:
    project_type = None
    current_stage = None
    stages: list[StageState] = []
    for line in markdown.splitlines():
        line = line.rstrip()
        if project_type is None and (m := _PROJECT_TYPE.search(line)):
            project_type = m.group(1).strip()
            continue
        if current_stage is None and (m := _CURRENT_STAGE.search(line)):
            current_stage = normalize_stage_name(m.group(1))
            continue
        if (m := _CHECK.match(line.strip())):
            checked = m.group(1).lower() == "x"
            body = m.group(2).strip()
            parts = _SPLIT.split(body, maxsplit=1)
            # 이름만 정규화한다 — note는 자유 서술이고, 그쪽의 엔티티는 키가
            # 아니라서 매칭을 깨지 않는다(마크다운 렌더가 처리한다).
            name = normalize_stage_name(parts[0])
            note = parts[1].strip() if len(parts) > 1 else None
            status = "completed" if checked else "pending"
            stages.append(StageState(name=name, status=status, note=note))

    if current_stage:
        pending = [s for s in stages if s.status == "pending"]
        exact = [s for s in pending if s.name == current_stage]
        if exact:
            exact[0].status = "in_progress"
        else:
            partial = [
                s for s in pending
                if s.name in current_stage or current_stage in s.name
            ]
            if partial:
                best = max(partial, key=lambda s: len(s.name))
                best.status = "in_progress"

    return ProjectState(project_type=project_type, current_stage=current_stage, stages=stages)
