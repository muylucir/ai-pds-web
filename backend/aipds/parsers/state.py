# backend/aipds/parsers/state.py
from __future__ import annotations
import re
from aipds.models import ProjectState, StageState

_PROJECT_TYPE = re.compile(r"\*\*Project Type\*\*:\s*(.+)")
_CURRENT_STAGE = re.compile(r"\*\*Current Stage\*\*:\s*(.+)")
_CHECK = re.compile(r"^- \[([ xX])\]\s*(.+)$")
_SPLIT = re.compile(r"\s+[—-]\s+")

#: 스테이지 체크리스트가 사는 섹션. 상류가 정한 이름이다
#: (`inception/workspace-detection.md`의 상태 파일 템플릿, 각 스테이지의
#: "Update State Tracking" 단계).
#:
#: **정확 일치가 아니라 포함이다.** 처음에는 `^## Stage Progress\s*$`였는데, 그러면
#: 헤딩이 장식되는 순간(`## Stage Progress (Discovery)`, `## 🟣 Stage Progress`)
#: 섹션을 못 찾아 아래 폴백이 켜지고 문서 전체를 훑는다 — 즉 이 수정이 없애려던
#: 증상이 **조용히** 되돌아온다. 에이전트가 헤딩을 장식하는 것은 관측된 습성이다
#: (`### 🟣 DISCOVERY PHASE`, `## Envision 진행 내역`). 대문자 표기도 마찬가지로
#: 관대하게 받는다.
#:
#: 과하게 물어도(`## Notes on Stage Progress`) 그 섹션의 체크박스가 스테이지로
#: 읽히는 정도이고, 못 물면 문서 전체가 스테이지가 된다 — 두 실패의 크기가 다르다.
#: `^## `는 `###`을 자연히 배제한다(세 번째 문자가 공백이어야 한다).
_PROGRESS_HEADER = re.compile(r"^## .*stage progress", re.IGNORECASE)

#: 그 섹션을 닫는 것: 다음 `##` 헤딩. `###`는 **닫지 않는다** — 상류 템플릿이
#: 섹션 안에 `### 🟣 DISCOVERY PHASE`를 두기 때문이다(envision.md:420-425).
_H2 = re.compile(r"^## ")

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
    """`aiplc-state.md` → 진행률 사이드바가 읽는 상태.

    **체크박스는 `## Stage Progress` 안에서만 스테이지다(2026-08-18 실측:
    test12345678).** 그 프로젝트의 사이드바에 14개가 떴다 — 스테이지 6개와,
    에이전트가 자기 기록용으로 만든 `## Envision 진행 내역`의 하위 단계 8개
    (`Step 0.1`~`Step 6`)가 섞여 있었다. 진행률이 스테이지 6개가 아니라 14개를
    세니 화면의 숫자도 뜻을 잃는다.

    왜 이제 드러났는가: 예전에는 `report_stage` 도구가 상태 파일을 우리 손으로
    upsert했고 그 쓰기 경로(`state_sync.upsert_stage`)는 `## Stage Progress`
    블록만 건드렸다. 2026-08-18에 그 도구가 훅으로 대체되면서 파일을 쓰는 것은
    **에이전트 단독**이 됐고, 상류 템플릿은 그 섹션을 `[Will be populated as
    workflow progresses]`로만 규정한다 — 즉 나머지 문서에 무엇을 적어도 규칙
    위반이 아니다. 가려 읽던 쪽이 없어졌으니 읽기 쪽이 가려야 한다.

    하위 단계 기록을 금지하지 않는 이유: 그것은 에이전트에게 유용한 장부이고,
    상류가 허용한다. 우리가 할 일은 그것을 스테이지로 **읽지 않는** 것이다.

    섹션이 아예 없으면 문서 전체를 훑는다 — 옛 동작이다. 빈 사이드바는 "질문
    파싱이 실패했는데 조용히 넘어가 질문이 사라졌다"와 같은 종류의 실패이고,
    잘못된 항목이 몇 개 섞이는 것보다 나쁘다.
    """
    project_type = None
    current_stage = None
    stages: list[StageState] = []
    # 섹션이 없는 문서를 위한 폴백: 헤딩을 한 번도 만나지 못하면 전부 훑는다.
    has_section = any(_PROGRESS_HEADER.match(ln.rstrip())
                      for ln in markdown.splitlines())
    in_progress_block = not has_section
    for line in markdown.splitlines():
        line = line.rstrip()
        if project_type is None and (m := _PROJECT_TYPE.search(line)):
            project_type = m.group(1).strip()
            continue
        if current_stage is None and (m := _CURRENT_STAGE.search(line)):
            current_stage = normalize_stage_name(m.group(1))
            continue
        if has_section:
            if _PROGRESS_HEADER.match(line):
                in_progress_block = True
                continue
            if in_progress_block and _H2.match(line):
                in_progress_block = False
                # 다음 섹션 헤딩 자체는 체크라인이 아니므로 계속 진행해도 된다.
        if not in_progress_block:
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
