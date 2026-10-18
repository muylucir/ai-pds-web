# backend/tests/test_agent_reconcile.py — UI events derived from the workspace.
#
# The checks here are ones that, until 2026-08-18, ran in tests/test_agent_tools.py against two
# MCP tools (`report_stage`, `handoff_prototype`). As the tools moved to hooks, **the behavior
# stayed the same and only the trigger changed** — so the checks moved too.
#
# One thing grew in the move: the tools presupposed "the model calls them", so the case where it
# does not call them could not be tested. Hooks and reconciliation have no not-called case, so this
# file checks that **as long as the file exists, the screen is right**.
import json
from pathlib import Path

from aipds.agent import reconcile


def _payloads(events):
    """Looks at the stage diff — the capability snapshot that rides along is covered by the dedicated test below."""
    out = [json.loads(e.payload) for e in events]
    for p in out:
        p.pop("capabilities", None)
    return out


# ---- Stages: derived from aiplc-state.md (formerly report_stage) ----


def test_every_stage_event_carries_the_same_capability_snapshot():
    md = ("- **Current Stage**: Prototype & Validation\n\n"
          "## Stage Progress\n"
          "- [x] Envision\n"
          "- [x] Solution Analysis\n"
          "- [ ] Prototype & Validation\n")
    events, _ = reconcile.stage_events(md, {})
    snaps = [json.loads(e.payload)["capabilities"] for e in events]
    assert len(events) == 3 and all(s == snaps[0] for s in snaps)
    assert [(c["key"], c["status"]) for c in snaps[0]] == [
        ("envision", "completed"),
        ("use_case_intake", "not_applicable"),
        ("prioritize", "not_applicable"),
        ("prototype", "in_progress"),
        ("product_strategy", "pending"),
        ("go_to_market", "pending"),
    ]


def test_a_state_file_becomes_stage_events():
    md = ("# AI-PLC State\n\n"
          "- **Current Stage**: Envision\n\n"
          "## Stage Progress\n"
          "- [x] Workspace Detection\n"
          "- [ ] Envision\n"
          "- [ ] Solution Analysis\n")
    events, cursor = reconcile.stage_events(md, {})
    assert _payloads(events) == [
        {"stage": "Workspace Detection", "status": "completed", "summary": ""},
        # parse_state_file folds the line Current Stage points to into in_progress.
        {"stage": "Envision", "status": "in_progress", "summary": ""},
        {"stage": "Solution Analysis", "status": "pending", "summary": ""},
    ]
    assert cursor["Envision"] == "in_progress"


def test_the_checklist_order_is_kept():
    """The checklist order is the methodology's stage order — sorting would stack the sidebar
    in an order different from the rules."""
    md = ("- **Current Stage**: Go-to-Market\n\n## Stage Progress\n"
          "- [x] Envision\n- [x] Product Strategy\n- [ ] Go-to-Market\n")
    events, _ = reconcile.stage_events(md, {})
    assert [p["stage"] for p in _payloads(events)] == [
        "Envision", "Product Strategy", "Go-to-Market"]


def test_an_unchanged_state_file_emits_nothing():
    """**The diff is the point of this design.** The frontend accumulates `stage` events
    (`[...prev, parsed]` in useWorkspaceStream.ts), so re-emitting the same state grows the
    sidebar list. The old `report_stage` tool fired twice if the model declared the same stage
    twice — this side is actually quieter."""
    md = "- **Current Stage**: Envision\n\n## Stage Progress\n- [ ] Envision\n"
    events, cursor = reconcile.stage_events(md, {})
    assert len(events) == 1
    again, cursor2 = reconcile.stage_events(md, cursor)
    assert again == []
    assert cursor2 == cursor


def test_only_the_stage_that_changed_is_emitted():
    first = "- **Current Stage**: Envision\n\n## Stage Progress\n- [ ] Envision\n- [ ] Solution Analysis\n"
    _, cursor = reconcile.stage_events(first, {})
    second = "- **Current Stage**: Solution Analysis\n\n## Stage Progress\n- [x] Envision\n- [ ] Solution Analysis\n"
    events, _ = reconcile.stage_events(second, cursor)
    assert _payloads(events) == [
        {"stage": "Envision", "status": "completed", "summary": ""},
        {"stage": "Solution Analysis", "status": "in_progress", "summary": ""},
    ]


def test_the_note_becomes_the_summary():
    md = ("- **Current Stage**: Envision\n\n## Stage Progress\n"
          "- [x] Envision — PR/FAQ 승인 완료\n")
    events, _ = reconcile.stage_events(md, {})
    assert _payloads(events)[0]["summary"] == "PR/FAQ 승인 완료"


def test_an_escaped_stage_name_is_unescaped():
    """2026-08-18 ws-d: the model sent `Prototype &amp; Validation`. A stage name is a
    **key**, not a display string, so a line with `&amp;` baked in has a different name from
    the correct line that follows, and one extra checkline appears.

    The old path had to normalize both the tool arguments and the file, but now the file is the
    only input, so a single pass on the read side does it (parsers/state.normalize_stage_name)."""
    md = ("- **Current Stage**: Prototype & Validation\n\n## Stage Progress\n"
          "- [ ] Prototype &amp; Validation\n")
    events, _ = reconcile.stage_events(md, {})
    payload = _payloads(events)[0]
    assert payload["stage"] == "Prototype & Validation"
    assert payload["status"] == "in_progress"


def test_an_escaped_then_clean_name_is_one_stage_not_two():
    """Normalization applies to the cursor too, so they merge into the same stage — progress
    does not count the same stage twice."""
    escaped = "- **Current Stage**: X\n\n## Stage Progress\n- [ ] Prototype &amp; Validation\n"
    _, cursor = reconcile.stage_events(escaped, {})
    clean = "- **Current Stage**: X\n\n## Stage Progress\n- [ ] Prototype & Validation\n"
    events, cursor2 = reconcile.stage_events(clean, cursor)
    assert events == []
    assert list(cursor2) == ["Prototype & Validation"]


def test_a_missing_or_empty_state_file_changes_nothing():
    """We do not clear the screen based on absence — the agent may be briefly emptying the file
    and rewriting it."""
    cursor = {"Envision": "in_progress"}
    for md in (None, "", "   \n"):
        events, out = reconcile.stage_events(md, cursor)
        assert events == []
        assert out == cursor


def test_a_state_file_with_no_checklist_emits_nothing():
    """With only Current Stage and no checklist, there is nothing to emit. Creating an item here
    would put a stage the rules did not define into the sidebar."""
    events, _ = reconcile.stage_events("- **Current Stage**: Envision\n", {})
    assert events == []


def test_read_state_returns_none_when_absent(tmp_path):
    assert reconcile.read_state(tmp_path) is None
    p = tmp_path / reconcile.STATE_KEY
    p.parent.mkdir(parents=True)
    p.write_text("- **Current Stage**: Envision\n", encoding="utf-8")
    assert "Envision" in (reconcile.read_state(tmp_path) or "")


# ---- Handoff: derived from the existence of build-instructions.md (formerly handoff_prototype) ----


def _spec(ws: Path, key: str) -> None:
    p = ws / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# spec", encoding="utf-8")


def test_the_single_prototype_layout_is_recognised(tmp_path):
    _spec(tmp_path, "aiplc-docs/discovery/prototype/prototype-spec.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototype/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert _payloads(events) == [{
        "slug": "prototype",
        "spec_path": "aiplc-docs/discovery/prototype/prototype-spec.md"}]
    assert cursor == {"prototype"}


def test_the_slugged_layout_is_recognised(tmp_path):
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-foo.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/build-instructions.md")
    events, _ = reconcile.prototype_events(tmp_path, set())
    assert _payloads(events) == [{
        "slug": "foo",
        "spec_path": "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-foo.md"}]


def test_build_instructions_without_a_spec_are_not_a_handoff(tmp_path):
    """The Prototypes tab builds cards from the **spec** (layout.discover in
    routes/prototypes.py). With only build instructions, the user sees an empty tab — that is
    why the old `handoff_prototype` checked that the spec existed, and that check has moved
    here."""
    _spec(tmp_path, "aiplc-docs/discovery/prototype/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert events == []
    assert cursor == set()


def test_a_spec_without_build_instructions_is_not_a_handoff(tmp_path):
    """When only the spec has been written (Step 1), it is not a handoff yet. Announcing here
    would show the card before the design is finished."""
    _spec(tmp_path, "aiplc-docs/discovery/prototype/prototype-spec.md")
    events, _ = reconcile.prototype_events(tmp_path, set())
    assert events == []


def test_a_handoff_is_announced_once(tmp_path):
    """Announcing twice puts two cards in the chat. Rewriting the build instructions is normal
    behavior, so a cursor is needed."""
    _spec(tmp_path, "aiplc-docs/discovery/prototype/prototype-spec.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototype/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert len(events) == 1
    again, _ = reconcile.prototype_events(tmp_path, cursor)
    assert again == []


def test_every_handed_off_prototype_is_announced(tmp_path):
    """Path B has three. Announcing only one silently drops the remaining cards."""
    for slug in ("a", "b", "c"):
        _spec(tmp_path, f"aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md")
        _spec(tmp_path, f"aiplc-docs/discovery/prototypes/{slug}/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert sorted(p["slug"] for p in _payloads(events)) == ["a", "b", "c"]
    assert cursor == {"a", "b", "c"}


def test_the_spec_path_is_the_real_one_not_a_computed_one(tmp_path):
    """Carries the path `discover` found on disk as is. Reassembling the path from the slug
    would put the layout convention in two places (proto/layout.py owns it alone)."""
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-foo.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/build-instructions.md")
    events, _ = reconcile.prototype_events(tmp_path, set())
    payload = _payloads(events)[0]
    assert (tmp_path / payload["spec_path"]).is_file()


def test_an_empty_workspace_is_quiet(tmp_path):
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert events == [] and cursor == set()


# ---- prototype_id_for: path → id ----


def test_prototype_id_for_matches_both_layouts():
    assert reconcile.prototype_id_for(
        "aiplc-docs/discovery/prototype/build-instructions.md") == "prototype"
    assert reconcile.prototype_id_for(
        "aiplc-docs/discovery/prototypes/foo/build-instructions.md") == "foo"


def test_prototype_id_for_rejects_everything_else():
    """If the turn is cut off on a different file, the agent stops mid-work — this judgment is
    the hook's turn-end condition, so it must be narrow."""
    for rel in ("aiplc-docs/discovery/prototype/prototype-spec.md",
                "aiplc-docs/discovery/prototype/design-context.md",
                "aiplc-docs/audit.md",
                # A build-instructions.md outside the layout is not a handoff.
                "aiplc-docs/discovery/build-instructions.md",
                "aiplc-docs/discovery/envision/build-instructions.md",
                "build-instructions.md"):
        assert reconcile.prototype_id_for(rel) is None, rel


# ---- Documents: derived from artifact writes under aiplc-docs/ (formerly submit_document) ----
#
# `submit_document` went away on 2026-08-21. The rationale for keeping it was "`version` and
# 'ready for review vs. intermediate save' are judgment, not parsing", but the actual instruction
# (discovery-config/CLAUDE.md) did not require judgment — it said to call it whenever a document
# is created or updated. Without judgment, the signal is 1:1 with "a document was written", and PostToolUse already sees that.
#
# On top of that, the tool **mostly went uncalled**: the frontend wrote at
# `useWorkspaceStream.ts:177` "The agent creates most documents with file_write alone, without
# submit_document (measured: prfaq.md etc.)" and put in a workaround. A third case of the same
# silence as stages and handoff.


def _doc(root: Path, rel: str, text: str = "# 문서\n본문\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_a_document_write_becomes_a_document_event(tmp_path):
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md")
    events, _ = reconcile.document_events(tmp_path, {})
    assert [e.kind for e in events] == ["document"]
    assert _payloads(events) == [
        {"path": "aiplc-docs/discovery/prfaq.md", "version": "1", "summary": ""}]


def test_the_record_keeping_files_are_not_documents(tmp_path):
    """Audit, state, and question files are not artifacts the doc panel follows.

    The same judgment as the frontend's `isDocPath` (useWorkspaceStream.ts) — if question files
    were included here, two versions of the same questions would show side by side on one screen.
    """
    for rel in ("aiplc-docs/audit.md",
                "aiplc-docs/aiplc-state.md",
                "aiplc-docs/discovery/envision/pain-point-questions.md",
                "aiplc-docs/discovery/notes.txt",
                "outside/other.md"):
        _doc(tmp_path, rel)
    events, cursor = reconcile.document_events(tmp_path, {})
    assert events == [] and cursor == {}


def test_the_same_content_is_announced_once(tmp_path):
    """Without a cursor, the banner pops up again on every write — the agent writing the same
    document several times in one turn is normal behavior (same reason stage_events uses a diff)."""
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md")
    first, cursor = reconcile.document_events(tmp_path, {})
    again, cursor2 = reconcile.document_events(tmp_path, cursor)
    assert len(first) == 1
    assert again == [] and cursor2 == cursor


def test_a_changed_document_gets_the_next_version(tmp_path):
    """The banner's dismiss remembers `version` (dismissedDocVersion in page.tsx), so the value must
    differ on every update — if it stays the same, once dismissed it never pops up again."""
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md", "# 문서\n초안\n")
    _, cursor = reconcile.document_events(tmp_path, {})
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md", "# 문서\n고친 본문\n")
    events, _ = reconcile.document_events(tmp_path, cursor)
    assert _payloads(events) == [
        {"path": "aiplc-docs/discovery/prfaq.md", "version": "2", "summary": ""}]


def test_an_empty_document_is_not_announced(tmp_path):
    """Keeps the reason the old tool rejected empty-file declarations — the user would read
    "작성됐습니다" while looking at an empty doc panel."""
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md", "   \n\n")
    events, cursor = reconcile.document_events(tmp_path, {})
    assert events == [] and cursor == {}
