# backend/aipds/capabilities.py — 스테이지 체크리스트 → AI-PDS의 6개 capability.
#
# **왜 묶는가.** `aiplc-state.md`의 `## Stage Progress`는 에이전트가 자유롭게 쓴
# 목록이다(상류 템플릿은 `[Will be populated as workflow progresses]`뿐). 실측 픽스처
# 다섯 개에서 그 길이가 5·6·11로 흔들렸고, 어느 것도 공식 문서의 6개와 같지 않았다 —
# Workspace Detection·Mode Selection 같은 진행용 단계와 Solution Analysis·Prototype
# Context Generation 같은 하위 단계가 같은 줄에 섰다. 사이드바가 그 목록을 그대로
# 세면 "몇 단계짜리 프레임워크인가"에 화면이 공식 문서와 다른 답을 한다.
#
# 공식 문서(docs/ai-pds-faq-ko.html Q1)는 6개 capability를 이 순서로 든다.
# Envision · Use Case Intake · Prioritize · Prototype · Product Strategy · Go-to-Market.
#
# **룰을 고치지 않고 읽는 쪽에서 묶는다.** 상태 파일은 그대로이고, 원래 목록은
# `ProjectState.stages`에 남는다(프로토타입 모드 판정 등이 원래 이름을 본다).
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from aipds.models import CapabilityState, StageState

#: (key, 표시 이름) — 공식 문서의 순서다. 표시 이름은 ko 문서도 번역하지 않는다.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("envision", "Envision"),
    ("use_case_intake", "Use Case Intake"),
    ("prioritize", "Prioritize"),
    ("prototype", "Prototype"),
    ("product_strategy", "Product Strategy"),
    ("go_to_market", "Go-to-Market"),
)

#: 스테이지 이름(소문자) 부분 문자열 → capability. **위에서부터 처음 맞는 것**이
#: 이긴다. 순서가 의미를 가진다:
#:   - 진행용 단계가 맨 앞이다 — `Discovery Mode Selection`이 다른 규칙에 걸리지 않게.
#:   - Go-to-Market이 Product Strategy보다 앞이다 — `Go-to-Market Strategy`.
#:   - Prioritize가 Intake보다 앞이다 — `Use Case Prioritization`.
#: Solution Analysis는 Envision으로 간다: 룰에서 PR/FAQ 직후 해법의 수를 정하는
#: 단계이고, 공식 6개에는 따로 없다. Discovery Document는 GTM 뒤에 문서를 닫는
#: 단계라 GTM에 넣는다. 실측 스테이지 이름은 ko 프로젝트도 영어였지만, 에이전트가
#: 번역할 때를 위해 한국어 표기를 함께 둔다.
_RULES: tuple[tuple[str | None, tuple[str, ...]], ...] = (
    (None, ("workspace detection", "mode selection", "워크스페이스", "모드 선택")),
    ("go_to_market", ("go-to-market", "go to market", "gtm", "discovery document",
                      "출시", "디스커버리 문서")),
    ("prioritize", ("prioritiz", "우선순위")),
    ("use_case_intake", ("intake", "유스케이스 수집", "활용 사례 수집")),
    ("prototype", ("prototype", "프로토타입")),
    ("product_strategy", ("strategy", "전략")),
    ("envision", ("envision", "pr/faq", "prfaq", "solution analysis", "솔루션 분석")),
)


def capability_of(stage_name: str) -> str | None:
    """스테이지 이름이 속한 capability key. 진행용 단계와 모르는 이름은 None."""
    low = stage_name.lower()
    for key, needles in _RULES:
        if any(n in low for n in needles):
            return key
    return None


def rollup(stages: Iterable[StageState]) -> list[CapabilityState]:
    """스테이지 목록 → 6개 capability의 상태. 항상 6개, 공식 순서.

    한 capability의 상태는 그 아래 묶인 스테이지들로 정한다:
      - 모두 completed → completed
      - 하나라도 in_progress이거나, 일부만 completed → in_progress
      - 그 밖(모두 pending) → pending

    묶인 스테이지가 **하나도 없으면** 두 경우다. 뒤쪽 capability에 스테이지가 있으면
    이 경로가 거치지 않은 것이다 — `not_applicable`. Path A(PR/FAQ에서 시작)는
    Intake·Prioritize를, Path B와 기존 PROTOTYPE 파일로 들어온 프로젝트는 Envision을
    건너뛴다. 뒤쪽에도 없으면 아직 목록에 오르지 않은 것이므로 `pending`이다 —
    에이전트는 목록을 진행하며 채우기도 하므로, 없다는 것만으로 건너뛴 것이 아니다.
    """
    from aipds.models import CapabilityState

    grouped: dict[str, list[StageState]] = {key: [] for key, _ in CAPABILITIES}
    for stage in stages:
        key = capability_of(stage.name)
        if key is not None:
            grouped[key].append(stage)

    keys = [key for key, _ in CAPABILITIES]
    last_listed = max((i for i, k in enumerate(keys) if grouped[k]), default=-1)
    out: list[CapabilityState] = []
    for i, (key, name) in enumerate(CAPABILITIES):
        rows = grouped[key]
        if not rows:
            status = "not_applicable" if i < last_listed else "pending"
            out.append(CapabilityState(key=key, name=name, status=status))
            continue
        done = [r for r in rows if r.status == "completed"]
        active = [r for r in rows if r.status == "in_progress"]
        if len(done) == len(rows):
            status = "completed"
        elif active or done:
            status = "in_progress"
        else:
            status = "pending"
        note = next((r.note for r in active if r.note), None)
        out.append(CapabilityState(key=key, name=name, status=status, note=note))
    return out
