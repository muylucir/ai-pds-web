# backend/aipds/agent/reconcile.py -- **derive** UI events from the workspace.
#
# **Why this module exists.** The stage sidebar and the Prototypes card were
# originally produced by MCP tools the model called (`report_stage`,
# `handoff_prototype`). A tool does nothing at all if the model does not call it,
# and that silence was measured twice:
#
#   - 2026-08-18 test123456: the PostToolUse hook ended the turn on the question
#     file write, so the `report_stage` batched into the same message never ran.
#     With no `aiplc-state.md` the badge stayed empty for the whole project, and
#     the resume turn says "continue from where you stopped" -- so nothing ever
#     prompted a retry.
#   - 2026-08-17 keumkang-v5: before `handoff_prototype` existed the agent asked
#     for credentials and listed prerequisites. The tab was pointed at zero times.
#
# So the test moves from "did the model declare it" to **"what does the disk
# say"**. Files do not get lost: a dropped batch, a turn ending, and the model
# forgetting all fail to delete a file.
#
# **A third tool joined them (2026-08-21).** This section used to justify keeping
# `submit_document` by saying its `version` and its "ready for review vs
# intermediate save" were judgement, not parsing. That justification was wrong --
# **the actual instruction did not ask for that judgement.** discovery-config said
# "call it after creating or updating a document", and with no judgement in play
# the signal is 1:1 with "a document was written" -- which PostToolUse already
# sees.
#
# The silence was measured just as plainly. The frontend had recorded it next to
# its own workaround: "the agent creates most documents with file_write alone,
# without submit_document (measured: prfaq.md and others)" --
# useWorkspaceStream.ts:177. The same class of failure as the two above, a third
# time.
#
# The `version` that remains is not judgement but **counting**: a content hash
# answers "did it change" and an ordinal answers "which revision is this"
# (`document_events`). That is more accurate than the string the model invented --
# the model could declare `v1` twice for the same document and nothing stopped it.
#
# **What is here and what is not.** Only signals derived from the workspace live
# here. The one remaining custom tool is `build_complete`, and it passes the same
# test: a build's last Write is indistinguishable from any other Write, so
# "finished" cannot be derived from a file (proto/tools.py).
#
# **Why a diff.** agent/tools.py's old header worried that "inferring from state
# file writes makes the UI flicker when a turn updates it several times". The
# worry was legitimate, but the answer is a diff rather than a tool: the frontend
# **accumulates** `stage` events (useWorkspaceStream.ts:189 `[...prev, parsed]`),
# so re-emitting the same state grows the list. Emit only the stages whose status
# actually changed and the problem disappears -- and it is quieter than the old
# tool was, which fired twice whenever the model declared the same stage twice.
#
# **Pure functions plus an explicit cursor.** The driver holds the cursors
# (`last`, `announced`); this module takes one and returns the next. There is no
# module-level state because the driver is per-project and because tests need to
# inject a cursor directly to exercise the diff paths.
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from aipds.models import AgentEvent
from aipds.parsers.state import parse_state_file
from aipds.proto import layout

_log = logging.getLogger("aipds.agent")

#: The artifact root. The name is the upstream ruleset's, and `STATE_KEY` lives
#: under it too.
DOCS_ROOT = "aiplc-docs"

#: The state file the upstream ruleset defines. Both `core-workflow.md`'s tree
#: and `prototype-validation.md` Step 10 use this path.
STATE_KEY = "aiplc-docs/aiplc-state.md"

#: The name of Step 3's final artifact. `prototype-validation.md:170` declares
#: it at a singular path (`aiplc-docs/discovery/prototype/build-instructions.md`);
#: under Path B the same name arrives inside a slug directory. Path assembly is
#: owned solely by `layout.artifact_dir`, so all this module knows is **the file
#: name**.
BUILD_INSTRUCTIONS = "build-instructions.md"


def stage_events(markdown: str | None,
                 last: dict[str, str]) -> tuple[list[AgentEvent], dict[str, str]]:
    """Full text of `aiplc-state.md` -> `stage` events for the stages whose status
    **changed**.

    `last` is the cursor meaning "this stage has already been emitted in this
    status". The returned dict is the next cursor, which the caller swaps in.

    File order is preserved. The checklist order IS the methodology's stage order
    (`core-workflow.md`'s Stage Progress), so sorting would stack the sidebar in an
    order the ruleset never specified.

    **No synthetic entry for `current_stage`.** `parse_state_file` already folds it
    into one checklist line as `in_progress` (exact match first, otherwise the
    longest partial match). A `current_stage` left outside that fold means a name
    with no checklist line, and inventing an entry for it would put a stage in the
    sidebar that the ruleset never defined -- this function does not overturn what
    the parser already decided.

    An empty file or None means no events. That is the fact "there are no stages",
    and that fact does not clear the screen (the agent may be mid-way through
    emptying and rewriting the file).
    """
    if not markdown or not markdown.strip():
        return [], last
    try:
        state = parse_state_file(markdown)
    except Exception:
        # A corrupt state file must not kill the turn: a stale badge beats a
        # failed turn, and the next write gives another chance.
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
    """Walk the files under `aiplc-docs/discovery/` as workspace-relative POSIX
    paths.

    `layout.discover` is designed to take exactly this shape (proto/layout.py), so
    the same function decides here and in the list route. This reads local rather
    than S3 because it has to judge a file the agent just wrote -- at that moment
    the disk is the source of truth (the old `tools._discovery_keys` recorded the
    same reasoning).
    """
    base = workspace / layout.DISCOVERY_PREFIX
    if not base.is_dir():
        return []
    return sorted(p.relative_to(workspace).as_posix()
                  for p in base.rglob("*") if p.is_file())


def prototype_id_for(rel: str) -> str | None:
    """The prototype id if this path is some prototype's `build-instructions.md`,
    else None.

    The inverse of `layout.artifact_dir`. Rather than parsing the path, it
    **assembles a candidate id and compares**: the layout convention is owned
    solely by that module, and a second regex here would put the rule in two
    places (that is the "duplicated in four places" cost layout.py's header
    describes).

    The candidate comes from the path: the last directory name is the id candidate
    (`prototype` for the singular layout, `{slug}` for the slugged one). Feed it to
    `artifact_dir`; if the original path comes back out, it matches.
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
    """Prototypes whose `build-instructions.md` is **on disk** -> their spec path.

    A handoff requires **both** the spec and the build instructions. Without the
    spec the Prototypes tab cannot build a card (routes/prototypes.py assembles the
    list via `layout.discover`) and the user sees an empty tab -- that is why the
    old `handoff_prototype` checked for the spec, and that check moved here.
    """
    keys = _discovery_keys(workspace)
    specs = layout.discover(keys)
    present = {k for k in keys if prototype_id_for(k) is not None}
    return {pid: spec for pid, spec in specs.items()
            if f"{layout.artifact_dir(pid)}/{BUILD_INSTRUCTIONS}" in present}


def prototype_events(workspace: Path,
                     announced: set[str]) -> tuple[list[AgentEvent], set[str]]:
    """Handoffs not yet announced -> `prototype_ready` events.

    `announced` is the cursor: announcing the same prototype twice puts two cards
    in the chat.
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


#: Record-keeping files, not documents. The upstream ruleset requires them, but
#: they are not artifacts the document panel should follow. The frontend recorded
#: why the question files belong in this list (useWorkspaceStream.ts): the answer
#: surface is the QuestionForm in the right panel, so also showing the markdown as
#: a document puts two versions of the same question on one screen -- and those
#: two can never agree.
_RECORD_KEEPING = ("audit.md", "aiplc-state.md")
_QUESTION_SUFFIX = "-questions.md"


def is_document(rel: str) -> bool:
    """Is this path an artifact the document panel should follow?

    **The same test** as the frontend's `isDocPath`. Having two copies is
    deliberate: the frontend one remains as a backstop hung off `file_changed` (for
    writes the hook cannot see, e.g. via Bash), while this one is the primary path
    for `document` events. Divergence is harmless -- both only ever move activeDoc
    in the same direction.
    """
    if not rel.startswith(f"{DOCS_ROOT}/") or not rel.endswith(".md"):
        return False
    name = rel.rsplit("/", 1)[-1]
    return name not in _RECORD_KEEPING and not name.endswith(_QUESTION_SUFFIX)


def document_events(workspace: Path,
                    seen: dict[str, tuple[int, str]],
                    ) -> tuple[list[AgentEvent], dict[str, tuple[int, str]]]:
    """`document` events for artifacts whose content **changed**. Replaces the old
    `submit_document`.

    `seen` is the cursor: path -> (version, content hash). A diff for the same
    reason `stage_events` uses one -- an agent writing the same document several
    times in one turn is normal behaviour, and emitting every time would re-raise
    the update banner that many times.

    **Why a version is needed.** The banner's close button remembers the `version`
    (page.tsx's `dismissedDocVersion`), so the value has to differ per update. If
    it stayed the same, dismissing once would suppress every later update to that
    document. Hence: the hash decides "did it change", the ordinal counts "which
    revision".

    **Limit: the ordinal only runs for the life of the process.** The cursor lives
    on the driver (an in-memory cursor like `_stage_status` and `_handed_off`) and
    resets to 1 when the backend restarts. The banner's `dismissedDocVersion` is
    page state and also clears on refresh, so in the common case the two reset
    together.

    Empty files are not announced -- the same reason the old tool refused an empty
    declaration: the user reads "it has been written" while looking at an empty
    document panel.
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
    """Full text of the workspace's state file, or None if absent.

    A read failure is also None: reconciliation is a backstop, and a backstop that
    fails the turn is not a backstop but a new cause of failure.
    """
    try:
        p = workspace / STATE_KEY
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except OSError:
        _log.exception("could not read %s", STATE_KEY)
        return None
