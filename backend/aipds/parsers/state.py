# backend/aipds/parsers/state.py
from __future__ import annotations
import re
from aipds.models import ProjectState, StageState

_PROJECT_TYPE = re.compile(r"\*\*Project Type\*\*:\s*(.+)")
_CURRENT_STAGE = re.compile(r"\*\*Current Stage\*\*:\s*(.+)")
_CHECK = re.compile(r"^(?P<indent>[ \t]*)- \[([ xX])\]\s*(.+)$")
#: Boundary between name and note: ` — ` or a status emoji. We split on emoji because
#: the agent appends the status after the name without a dash, as in `Envision ✅ 승인 완료`
#: (observed). Left as is, the emoji and the note become part of the name (= key), show
#: up verbatim in the sidebar, and break matching against Current Stage. The emoji stays
#: on the note side.
_SPLIT = re.compile(r"\s+[—-]\s+|\s+(?=[✅⏳🔄❌⚠])")

#: The section where the stage checklist lives. The name is set by upstream (the state
#: file template in `inception/workspace-detection.md`, and each stage's "Update State
#: Tracking" step).
#:
#: **Containment, not exact match.** It was originally `^## Stage Progress\s*$`, but
#: then the moment the heading got decorated (`## Stage Progress (Discovery)`, `## 🟣
#: Stage Progress`) the section was not found, the fallback below kicked in and scanned
#: the whole document; that is, the symptom this fix was meant to remove came back
#: **silently**. Decorating headings is an observed habit of the agent (`### 🟣 DISCOVERY
#: PHASE`, `## Envision 진행 내역`). Capitalization is likewise accepted leniently.
#:
#: Over-matching (`## Notes on Stage Progress`) at worst makes that section's checkboxes
#: read as stages; under-matching makes the whole document stages. The two failures
#: differ in size. `^## ` naturally excludes `###` (the third character must be a
#: space).
_PROGRESS_HEADER = re.compile(r"^## .*stage progress", re.IGNORECASE)

#: What closes that section: the next `##` heading. `###` does **not** close it, because
#: the upstream template places `### 🟣 DISCOVERY PHASE` inside the section
#: (envision.md:420-425).
_H2 = re.compile(r"^## ")

#: Escapes that actually appeared in stage names. Narrowed to the 5 XML predefined
#: entities.
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">",
             "&quot;": '"', "&#39;": "'", "&apos;": "'"}
_ENTITY_RE = re.compile("|".join(re.escape(k) for k in _ENTITIES))


def normalize_stage_name(raw: str) -> str:
    """Unescape HTML entities in a stage name and trim surrounding whitespace.

    **Why this is needed (observed 2026-08-18: ws-d).** The model sent `"stage":
    "Prototype &amp; Validation"` to `report_stage`. The same call's `summary` and the
    preceding `Solution Analysis` call had a normal `&`, so the escape happened in that
    one field only. Neither the ruleset nor our code contains `&amp;` anywhere
    (exhaustive grep).

    A stage name is not a display string but a **key**. Left untouched, three things break
    together:

    1. The sidebar shows `Prototype &amp; Validation` verbatim.
    2. Name matching in `parse_state_file` below goes wrong: a line with `&amp;` in it
       has a different name from the following **correct** line, so an extra check line
       appears and progress counts the same stage twice.
    3. The partial-containment fallback (`_names_match`) may pick the wrong line as
       the longest match.

    **Normalizing on the read side is the point.** Until 2026-08-18 the write side (the
    `report_stage` tool) also called this, but that tool was replaced by a hook and now
    the agent writes `aiplc-state.md`; that is, there is no write path left for us to
    touch at all. Files already saved with `&amp;` baked in are also healed by read-side
    normalization without modifying the file (ws-d's state file is such a case).

    Why not `html.unescape`: it leniently unescapes forms without a semicolon such as
    `&notin` and hundreds of named entities, so it would silently turn a name containing
    `&copy` into `©`. Stage names only need the five above, and a narrow substitution
    does not create unexpected name variants. Because it is done in a single `re.sub`,
    there is also no accident from substitution order such as `&amp;lt;` → `<`.
    """
    return _ENTITY_RE.sub(lambda m: _ENTITIES[m.group(0)], raw).strip()

def _top_indent(markdown: str, has_section: bool) -> int:
    """Indentation of stage checkboxes: the shallowest checkbox in the section.

    **An indented checkbox is not a stage but a sub-step of that stage.** Writing steps
    such as `  - [x] Step 0: …` under a stage is an observed habit of the agent
    (observed: in two projects 5 stages were read as 18 and 20), and while we read with
    indentation stripped, those steps mixed into the sidebar and progress as stages. Why
    "the shallowest" rather than 0: so a document that indents its whole list does not
    lose its stages.
    """
    depths = []
    inside = not has_section
    for line in markdown.splitlines():
        line = line.rstrip()
        if has_section:
            if _PROGRESS_HEADER.match(line):
                inside = True
                continue
            if inside and _H2.match(line):
                inside = False
        if inside and (m := _CHECK.match(line)):
            depths.append(len(m.group("indent").expandtabs(4)))
    return min(depths) if depths else 0


def parse_state_file(markdown: str) -> ProjectState:
    """`aiplc-state.md` → the state the progress sidebar reads.

    **Checkboxes are stages only inside `## Stage Progress` (observed 2026-08-18:
    test12345678).** That project's sidebar showed 14 items: 6 stages mixed with 8
    sub-steps (`Step 0.1`–`Step 6`) from `## Envision 진행 내역`, which the agent created
    for its own records. With progress counting 14 instead of 6 stages, the number on
    screen loses its meaning too.

    Why it surfaced only now: previously the `report_stage` tool upserted the state file
    on our side, and that write path (`state_sync.upsert_stage`) touched only the `##
    Stage Progress` block. On 2026-08-18 that tool was replaced by a hook, so the
    **agent alone** writes the file, and the upstream template defines that section only
    as `[Will be populated as workflow progresses]`; that is, whatever goes in the rest
    of the document is not a rule violation. The side that used to filter is gone, so
    the read side must filter.

    Why we do not forbid recording sub-steps: it is a useful ledger for the agent, and
    upstream allows it. Our job is to **not read** it as stages.

    If the section is missing entirely, the whole document is scanned (the old
    behavior). An empty sidebar is the same kind of failure as "question parsing failed,
    was silently skipped, and the questions disappeared", and is worse than a few wrong
    items mixed in.
    """
    project_type = None
    current_stage = None
    stages: list[StageState] = []
    # Fallback for documents without the section: if the heading is never seen, scan
    # everything.
    has_section = any(_PROGRESS_HEADER.match(ln.rstrip())
                      for ln in markdown.splitlines())
    in_progress_block = not has_section
    top_indent = _top_indent(markdown, has_section)
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
                # The next section heading itself is not a check line, so it is fine to
                # continue.
        if not in_progress_block:
            continue
        m = _CHECK.match(line)
        if m and len(m.group("indent").expandtabs(4)) == top_indent:
            checked = m.group(2).lower() == "x"
            body = m.group(3).strip()
            parts = _SPLIT.split(body, maxsplit=1)
            # Normalize only the name; note is free text, and entities there are not
            # keys, so they do not break matching (the markdown renderer handles them).
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
