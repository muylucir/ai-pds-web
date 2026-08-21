# backend/aipds/parsers/state.py
from __future__ import annotations
import re
from aipds.models import ProjectState, StageState

_PROJECT_TYPE = re.compile(r"\*\*Project Type\*\*:\s*(.+)")
_CURRENT_STAGE = re.compile(r"\*\*Current Stage\*\*:\s*(.+)")
_CHECK = re.compile(r"^- \[([ xX])\]\s*(.+)$")
_SPLIT = re.compile(r"\s+[—-]\s+")

#: The section the stage checklist lives in. The name is the upstream ruleset's (the
#: state file template in `inception/workspace-detection.md`, and each stage's
#: "Update State Tracking" step).
#:
#: **Containment, not an exact match.** This started as `^## Stage Progress\s*$`,
#: which fails to find the section the moment the heading is decorated
#: (`## Stage Progress (Discovery)`, `## 🟣 Stage Progress`) -- the fallback below then
#: kicks in and scans the whole document, **silently** restoring the very symptom this
#: fix exists to remove. Decorating headings is observed agent behaviour
#: (`### 🟣 DISCOVERY PHASE`, `## Envision progress log`). Capitalisation is accepted
#: just as leniently.
#:
#: Matching too much (`## Notes on Stage Progress`) only means that section's
#: checkboxes are read as stages; failing to match means the whole document becomes
#: stages -- the two failures are not the same size. `^## ` naturally excludes `###`
#: (the third character has to be a space).
_PROGRESS_HEADER = re.compile(r"^## .*stage progress", re.IGNORECASE)

#: What closes that section: the next `##` heading. `###` does **not** close it,
#: because the upstream template puts `### 🟣 DISCOVERY PHASE` inside the section
#: (envision.md:420-425).
_H2 = re.compile(r"^## ")

#: The escapes actually observed in stage names. Narrowed to the five predefined
#: XML entities.
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">",
             "&quot;": '"', "&#39;": "'", "&apos;": "'"}
_ENTITY_RE = re.compile("|".join(re.escape(k) for k in _ENTITIES))


def normalize_stage_name(raw: str) -> str:
    """Undo HTML entities in a stage name and trim surrounding whitespace.

    **Why it is needed (measured 2026-08-18: hpt-sarang).** The model sent
    `"stage": "Prototype &amp; Validation"` to `report_stage`. The `summary` in the
    same call and the preceding `Solution Analysis` call both had a plain `&`, so the
    escaping happened in that one field only -- there is not a single `&amp;` in the
    ruleset or in our code (checked exhaustively by grep).

    A stage name is not a display string but a **key**. Left alone, three things break
    together:

    1. The sidebar shows `Prototype &amp; Validation` verbatim.
    2. Name matching in `parse_state_file` below goes out of step: a line carrying
       `&amp;` has a different name from the **correct** line that follows, so an extra
       check line appears and the progress count counts the same stage twice.
    3. The partial-containment fallback (`_names_match`) can pick the wrong line as the
       longest match.

    **Normalising on the read side is the point.** Until 2026-08-18 the write side (the
    `report_stage` tool) called this too, but that tool was replaced by a hook and what
    writes `aiplc-state.md` now is the agent -- meaning there is no write path left for
    us to touch. Files already stored with `&amp;` baked in are healed by the read-side
    normalisation without editing the file (hpt-sarang's state file is that case).

    Why not `html.unescape`: it leniently undoes semicolon-less forms like `&notin`
    plus hundreds of named entities, so it would quietly turn a name containing
    `&copy` into `©`. A stage name needs only the five above, and a narrow substitution
    creates no unexpected name variants. It is done in a single `re.sub`, so there is
    also no ordering accident such as `&amp;lt;` -> `<`.
    """
    return _ENTITY_RE.sub(lambda m: _ENTITIES[m.group(0)], raw).strip()

def parse_state_file(markdown: str) -> ProjectState:
    """`aiplc-state.md` -> the state the progress sidebar reads.

    **A checkbox is a stage only inside `## Stage Progress` (measured 2026-08-18:
    test12345678).** That project's sidebar showed 14 entries -- the 6 stages mixed
    with 8 sub-steps (`Step 0.1` through `Step 6`) from an
    `## Envision progress log` section the agent had created for its own records. With
    the progress count counting 14 rather than 6 stages, the number on screen loses its
    meaning too.

    Why this only surfaced now: the `report_stage` tool used to upsert the state file
    with our own code, and that write path (`state_sync.upsert_stage`) touched the
    `## Stage Progress` block only. When that tool was replaced by a hook on
    2026-08-18, writing the file became **the agent's alone**, and the upstream template
    specifies that section merely as `[Will be populated as workflow progresses]` --
    meaning anything written elsewhere in the document breaks no rule. The side that
    used to read selectively is gone, so the read side has to select.

    Why sub-step logs are not forbidden: they are a useful ledger for the agent and
    upstream permits them. Our job is simply **not to read them as stages**.

    With no such section at all it scans the whole document -- the old behaviour. An
    empty sidebar is the same class of failure as "question parsing failed, was passed
    over quietly, and the question disappeared", and that is worse than a few wrong
    entries mixed in.
    """
    project_type = None
    current_stage = None
    stages: list[StageState] = []
    # Fallback for a document with no such section: if the heading is never seen,
    # scan everything.
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
                # The next section heading is not itself a check line, so continuing
                # is fine.
        if not in_progress_block:
            continue
        if (m := _CHECK.match(line.strip())):
            checked = m.group(1).lower() == "x"
            body = m.group(2).strip()
            parts = _SPLIT.split(body, maxsplit=1)
            # Only the name is normalised: the note is free prose, and entities there
            # are not keys and so cannot break matching (the markdown renderer handles
            # them).
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
