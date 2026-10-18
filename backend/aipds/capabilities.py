# backend/aipds/capabilities.py — stage checklist → AI-PDS's 6 capabilities.
#
# **Why group them.** `## Stage Progress` in `aiplc-state.md` is a list the agent
# writes freely (the upstream template has only
# `[Will be populated as workflow progresses]`). Across five real fixtures its length
# fluctuated among 5, 6, and 11, and none matched the 6 in the official docs —
# progress stages such as Workspace Detection and Mode Selection stood on the same
# level as sub-stages such as Solution Analysis and Prototype Context Generation. If
# the sidebar counted that list as is, the screen would give a different answer from
# the official docs to "how many stages does this framework have".
#
# The official docs (docs/ai-pds-faq-ko.html Q1) list the 6 capabilities in this
# order.
# Envision · Use Case Intake · Prioritize · Prototype · Product Strategy · Go-to-Market.
#
# **On screen they are "phases"** (en; ko uses "단계"). The AI-PDS team calls these 6
# phases, and the screen follows the framework team's terminology. The name in code
# (capability) follows the FAQ and is also an API field (`ProjectState.capabilities`),
# so it stays as is.
#
# **Group on the reading side without changing the rules.** The state file stays as
# is, and the original list remains in `ProjectState.stages` (prototype-mode detection
# and the like look at the original names).
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from aipds.models import CapabilityState, StageState

#: (key, display name) — in the official docs' order. Display names are not translated
#: even in the ko docs.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("envision", "Envision"),
    ("use_case_intake", "Use Case Intake"),
    ("prioritize", "Prioritize"),
    ("prototype", "Prototype"),
    ("product_strategy", "Product Strategy"),
    ("go_to_market", "Go-to-Market"),
)

#: Stage name (lowercase) substring → capability. **The first match from the top**
#: wins. Order is significant:
#:   - Progress stages come first — so `Discovery Mode Selection` does not hit another
#:     rule.
#:   - Go-to-Market comes before Product Strategy — `Go-to-Market Strategy`.
#:   - Prioritize comes before Intake — `Use Case Prioritization`.
#: Solution Analysis maps to Envision: in the rules it is the stage right after PR/FAQ
#: that decides the number of solutions, and it has no slot of its own among the
#: official 6. Discovery Document is the stage that closes the document after GTM, so
#: it goes into GTM. Real stage names were English even in ko projects, but Korean
#: spellings are included as well for when the agent translates them.
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
    """The capability key a stage name belongs to. None for progress stages and
    unknown names."""
    low = stage_name.lower()
    for key, needles in _RULES:
        if any(n in low for n in needles):
            return key
    return None


def rollup(stages: Iterable[StageState]) -> list[CapabilityState]:
    """Stage list → state of the 6 capabilities. Always 6, in the official order.

    A capability's state is determined by the stages grouped under it:
      - all completed → completed
      - any in_progress, or only some completed → in_progress
      - otherwise (all pending) → pending

    When **no** stages are grouped under it, there are two cases. If a later
    capability has stages, this path skipped it — `not_applicable`. Path A (starting
    from a PR/FAQ) skips Intake and Prioritize; Path B and projects that came in with
    an existing PROTOTYPE file skip Envision. If the later ones have none either, it
    simply has not been listed yet, so it is `pending` — the agent also fills in the
    list as it progresses, so absence alone does not mean it was skipped.
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
