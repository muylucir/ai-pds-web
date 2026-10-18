# backend/aipds/agent/reconcile.py — **derives** UI events from the workspace.
#
# **Why this module exists.** The stage sidebar and the Prototypes cards were originally
# produced by MCP tools the model calls (`report_stage`, `handoff_prototype`). If the model
# does not call a tool, nothing happens, and that silence was measured twice:
#
#   - 2026-08-18 test123456: when the PostToolUse hook ended the turn on a question-file write,
#     the `report_stage` batched in the same message did not run. With no `aiplc-state.md`,
#     the badge stayed empty for the whole project, and the resume turn picks up "from where
#     it stopped", so there was no trigger to recover.
#   - 2026-08-17 ws-a-v5: back when there was no `handoff_prototype`, the agent asked for
#     credentials and listed prerequisites. The tab guidance appeared 0 times.
#
# So we move the criterion from "did the model declare it" to **"what does the disk say"**.
# Files do not get lost — neither a batch drop, nor a turn ending, nor the model's
# forgetfulness can erase a file.
#
# **The third tool came here too (2026-08-21).** This section used to give the reason
# `submit_document` remained as "its `version` and 'ready for review vs. intermediate save'
# are judgment, not parsing". That rationale was wrong — **the actual instruction did not
# require that judgment.** discovery-config said "call it whenever you create or update a
# document", and without judgment the signal is 1:1 with "a document was written". And that
# was something PostToolUse already sees.
#
# The silence had been measured in the same way. The frontend wrote it down along with a workaround:
# "The agent creates most documents with file_write alone, without submit_document
# (measured: prfaq.md etc.)" — useWorkspaceStream.ts:177. A third case of the same kind as the two above.
#
# The remaining `version` is not judgment but **counting**: a content hash answers "did it change"
# and an ordinal counts "which one is this" (`document_events`). It is more accurate than the
# string the model used to make up — the model could declare `v1` twice for the same document,
# and nothing prevented it.
#
# **What is here and what is not.** Only signals derived from the workspace are here. The one
# remaining custom tool is `build_complete`, and it passes the same test — a build's last Write
# is indistinguishable from other Writes, so "done" cannot be derived from files (proto/tools.py).
#
# **Why diff.** The old header of agent/tools.py worried that "inferring backward from state
# file writes makes the UI flicker when it is updated several times in one turn". The worry was
# legitimate, but the fix is a diff, not a tool: the frontend **accumulates** `stage` events
# (useWorkspaceStream.ts:189 `[...prev, parsed]`), so re-emitting the same state grows the list.
# Emitting only stages whose state actually changed makes that problem go away, and it is even
# quieter than the old tool — the tool fired twice if the model declared the same stage twice.
#
# **Pure functions + explicit cursors.** The driver holds the cursors (`last`, `announced`), and
# this module takes them and returns new cursors. There is no module-global state because there
# is one driver per project, and tests must pass cursors in directly to exercise the diff paths.
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from aipds.models import AgentEvent
from aipds.parsers.state import parse_state_file
from aipds.proto import layout

_log = logging.getLogger("aipds.agent")

#: Artifact root. A name set by the upstream rules, and `STATE_KEY` is under it too.
DOCS_ROOT = "aiplc-docs"

#: The state file set by the upstream rules. The tree in `core-workflow.md` and
#: Step 10 of `prototype-validation.md` use this path.
STATE_KEY = "aiplc-docs/aiplc-state.md"

#: Name of Step 3's last artifact. `layout` alone owns the name and path assembly
#: — the build session (proto/session.py) hands the same file to the builder, so there must not be two copies.
BUILD_INSTRUCTIONS = layout.BUILD_INSTRUCTIONS


def stage_events(markdown: str | None,
                 last: dict[str, str]) -> tuple[list[AgentEvent], dict[str, str]]:
    """Full text of `aiplc-state.md` → `stage` events for stages whose state **changed**.

    `last` is a cursor meaning "this stage has already been emitted in this state". The
    returned dict is the new cursor, and the caller replaces the old one with it.

    File order is preserved. The checklist order is the methodology's stage order
    (Stage Progress in `core-workflow.md`), so sorting would stack the sidebar in an order
    different from the rules.

    **No separate `current_stage` entry is created.** `parse_state_file` already folds it
    into one checklist line as `in_progress` (exact match first, otherwise the longest
    partial match). A `current_stage` outside that fold means a name with no line in the
    checklist, and creating an item for it would put a stage the rules did not define into
    the sidebar — we do not overturn here what the parser already decided.

    An empty file/None means no events. That is the fact "there are no stages", and we do
    not clear the screen based on absence (the agent may be briefly emptying the file and
    rewriting it).
    """
    if not markdown or not markdown.strip():
        return [], last
    try:
        state = parse_state_file(markdown)
    except Exception:
        # Do not kill the turn over a corrupted state file — a stale badge is better than a
        # failed turn, and the next write gives another chance.
        _log.exception("could not parse %s — leaving the stage badges as they are",
                       STATE_KEY)
        return [], last
    events: list[AgentEvent] = []
    cursor = dict(last)
    # A **snapshot** of the 6 capabilities the sidebar counts. The same value goes on every
    # event — the frontend only needs the last one, and the grouping rules (aipds/capabilities.py)
    # are not duplicated. Why it is not a new event kind: the snapshot changes exactly when some
    # stage's state changes.
    capabilities = [c.model_dump() for c in state.capabilities]
    for stage in state.stages:
        if cursor.get(stage.name) == stage.status:
            continue
        cursor[stage.name] = stage.status
        events.append(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage.name, "status": stage.status,
             "summary": stage.note or "", "capabilities": capabilities},
            ensure_ascii=False)))
    return events, cursor


def _discovery_keys(workspace: Path) -> list[str]:
    """Walk files under `aiplc-docs/discovery/` as workspace-relative POSIX paths.

    `layout.discover` is designed to take this shape (proto/layout.py), so judgment can
    use **the same function** as the list route. It reads local disk rather than S3 because
    a file the agent just Wrote must be judged right away — at that moment the source of
    truth is the disk (the old `tools._discovery_keys` recorded the same reasoning).
    """
    base = workspace / layout.DISCOVERY_PREFIX
    if not base.is_dir():
        return []
    return sorted(p.relative_to(workspace).as_posix()
                  for p in base.rglob("*") if p.is_file())


def prototype_id_for(rel: str) -> str | None:
    """If this path is some prototype's `build-instructions.md`, that id; otherwise None.

    The inverse of `layout.artifact_dir`. Instead of parsing the path directly, it **assembles
    from candidate ids and compares** — that module alone owns the layout convention, and
    writing another regex here would put the rule in two places (that is the cost of
    "replicated in four places" in the layout.py header).

    Candidates come from the path: the last directory name is the id candidate (`prototype`
    for the singular case, `{slug}` for slugs). If feeding it into `artifact_dir` yields the original path, it matches.
    """
    p = Path(rel)
    if p.name != BUILD_INSTRUCTIONS:
        return None
    candidate = p.parent.name
    if not candidate:
        return None
    if layout.build_instructions_key(candidate) != rel:
        return None
    return candidate


def handed_off(workspace: Path) -> dict[str, str]:
    """Prototypes whose `build-instructions.md` **is on disk** → their spec paths.

    Handoff requires **both** the spec and the build instructions. Without a spec the
    Prototypes tab cannot build a card (routes/prototypes.py builds the list with
    `layout.discover`), so the user sees an empty tab — that is why the old
    `handoff_prototype` checked that the spec existed, and that check has moved here.
    """
    keys = _discovery_keys(workspace)
    specs = layout.discover(keys)
    present = {k for k in keys if prototype_id_for(k) is not None}
    return {pid: spec for pid, spec in specs.items()
            if layout.build_instructions_key(pid) in present}


def prototype_events(workspace: Path,
                     announced: set[str]) -> tuple[list[AgentEvent], set[str]]:
    """Turn handoffs not yet announced into `prototype_ready` events.

    `announced` is the cursor — announcing the same prototype twice puts two cards in the chat.
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


#: Records that are not documents. These are files the upstream rules require, not artifacts
#: for the doc panel to follow — why question files are included is written down in the frontend
#: (useWorkspaceStream.ts): the answer screen is the QuestionForm in the right panel, so showing
#: the markdown as a document too would put two versions of the same questions on one screen, and the two can never match.
_RECORD_KEEPING = ("audit.md", "aiplc-state.md")
_QUESTION_SUFFIX = "-questions.md"


def is_document(rel: str) -> bool:
    """Is this path an artifact the doc panel should follow.

    **The same judgment** as the frontend's `isDocPath`. Having two copies is intentional: the
    frontend one remains as a backstop hooked onto `file_changed` (writes via Bash that the hook
    cannot see), and this one is the main path for `document` events. Divergence is harmless —
    both only ever move activeDoc in the same direction.
    """
    if not rel.startswith(f"{DOCS_ROOT}/") or not rel.endswith(".md"):
        return False
    name = rel.rsplit("/", 1)[-1]
    return name not in _RECORD_KEEPING and not name.endswith(_QUESTION_SUFFIX)


def document_events(workspace: Path,
                    seen: dict[str, tuple[int, str]],
                    ) -> tuple[list[AgentEvent], dict[str, tuple[int, str]]]:
    """`document` events for artifacts whose content **changed**. Replaces the old `submit_document`.

    `seen` is the cursor: path → (version, content hash). It is a diff for the same reason as
    `stage_events` — the agent writing the same document several times in one turn is normal
    behavior, and emitting every time would pop the update banner up that many times again.

    **Why a version is needed.** The banner's dismiss button remembers `version`
    (`dismissedDocVersion` in page.tsx), so the value must differ on every update. If the value
    stays the same, once dismissed, no further update to that document can be announced. So a
    hash judges "did it change" and an ordinal counts "which one is this".

    **Limitation: the ordinal continues only within the process lifetime.** The cursor is held by
    the driver (an in-memory cursor like `_stage_status` and `_handed_off`) and goes back to 1
    when the backend restarts. The banner's `dismissedDocVersion` is also page state and is
    cleared on refresh, so in the common case both reset together.

    Empty files are not announced — for the same reason the old tool rejected empty
    declarations: the user would read "작성됐습니다" while looking at an empty doc panel.
    """
    events: list[AgentEvent] = []
    cursor = dict(seen)
    root = workspace / DOCS_ROOT
    if not root.is_dir():
        return events, cursor
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(workspace).as_posix()
        if not is_document(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = cursor.get(rel)
        if previous is not None and previous[1] == digest:
            continue
        version = (previous[0] if previous else 0) + 1
        cursor[rel] = (version, digest)
        events.append(AgentEvent(kind="document", payload=json.dumps(
            {"path": rel, "version": str(version), "summary": ""},
            ensure_ascii=False)))
    return events, cursor


def read_state(workspace: Path) -> str | None:
    """Full text of the workspace's state file. None if absent.

    A read failure is also None — reconciliation is a backstop, and if it failed the turn it
    would become a new cause of failure rather than a backstop.
    """
    try:
        p = workspace / STATE_KEY
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except OSError:
        _log.exception("could not read %s", STATE_KEY)
        return None
