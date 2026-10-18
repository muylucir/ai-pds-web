# Reproduce the upstream (aws-samples/sample-ai-plc) layout in the workspace:
#   core-workflow.md -> CLAUDE.md,  aws-aiplc-rule-details/ -> under the same name.
# core-workflow.md:18 assumes a CWD-relative path with
# `Rule details location: ./aws-aiplc-rule-details/`, so the rules must be in the workspace
# for the agent to read that path as is.
from pathlib import Path

import pytest

from aipds.agent.workspace_rules import place_rules


#: The first heading of the real language directive. Why we do not use fixture stubs (the old
#: `KO-DIRECTIVE`/`EN-DIRECTIVE`): the directive moved out of the ruleset tree (it is now a
#: code constant), and for the fixture to imitate it we would have to monkeypatch a module
#: attribute. Using the real thing also removes drift between the fixture and the real
#: thing — that drift is why `test_works_against_the_real_repo_rules` exists.
_KO_MARK = "# 언어 규약"
_EN_MARK = "# Language convention"


def _directive(language: str) -> str:
    """The language directive is **code** (`workspace_rules.LANGUAGE_DIRECTIVES`).

    Moved from `aipds/agent/language/{ko,en}.md` on 2026-08-19. A failure path that existed
    only because it was a file goes away — a string literal cannot go missing, so
    "assemble without a directive" becomes structurally impossible (the old
    `test_raises_when_the_language_directive_is_missing` had to monkeypatch the lookup path to
    create that state, and its docstring itself noted that it "only happens when the
    installation is broken").

    And the two editions sit side by side in one file. While they were two separate files
    they diverged — ko was 3,389 characters / en 1,310, and en ended with "nothing to
    translate", carrying no template-handling judgment at all.
    """
    from aipds.agent.workspace_rules import LANGUAGE_DIRECTIVES
    return LANGUAGE_DIRECTIVES[language]


def _rules(tmp_path: Path) -> Path:
    """A fixture imitating the repo's steering-files/aiplc-rules layout.

    **The language directive is not here.** Upstream `aiplc-rules/` has only `.gitkeep`,
    `aws-aiplc-rules/`, and `aws-aiplc-rule-details/` (verified), and our directive lives in
    `agent/workspace_rules.LANGUAGE_DIRECTIVES`. The fixture must reflect that so the
    operation "swap out the whole ruleset" also looks safe in tests.
    """
    rules = tmp_path / "rules"
    (rules / "aws-aiplc-rules").mkdir(parents=True)
    (rules / "aws-aiplc-rules" / "core-workflow.md").write_text(
        "# DISCOVERY PHASE WORKFLOW", encoding="utf-8")
    details = rules / "aws-aiplc-rule-details" / "common"
    details.mkdir(parents=True)
    (details / "process-overview.md").write_text("OVERVIEW", encoding="utf-8")
    return rules


def test_claude_md_is_language_directive_then_core_workflow(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    # The language directive comes **first**. In the failure recorded by
    # discovery-config/CLAUDE.md, the CRITICAL of a template "closer in context" beat the
    # language directive, so here the language goes at the very front as the premise of the
    # whole document.
    assert text.index(_KO_MARK) < text.index("# DISCOVERY PHASE WORKFLOW")
    assert _EN_MARK not in text


def test_english_project_gets_the_english_directive(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="en")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert _EN_MARK in text
    # If the Korean directive remained, the two directives would conflict — that is the shape of 7f33652's failure.
    assert _KO_MARK not in text


def test_the_two_languages_produce_different_claude_md(tmp_path):
    rules = _rules(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    place_rules(str(a), str(rules), language="ko")
    place_rules(str(b), str(rules), language="en")
    assert (a / "CLAUDE.md").read_text(encoding="utf-8") \
        != (b / "CLAUDE.md").read_text(encoding="utf-8")


def test_defaults_to_korean(tmp_path):
    # Callers that pass no argument (old code, tests) keep the existing behavior.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)))
    assert _KO_MARK in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_an_unknown_language_falls_back_to_korean(tmp_path):
    # Even if a corrupted manifest carries an arbitrary string, it does not run without rules.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="klingon")
    assert _KO_MARK in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_switching_language_rewrites_claude_md(tmp_path):
    # The assembled result is not a source file, so the size-comparison optimization does not apply.
    # Even if the two language directives happen to be the same size, it must always be rewritten.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    place_rules(str(ws), str(rules), language="en")
    assert _EN_MARK in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_the_language_directives_live_outside_the_upstream_ruleset():
    """**Pins the location we moved it to on 2026-08-18.**

    Upstream `aiplc-rules/` has only `.gitkeep`, `aws-aiplc-rules/`, and
    `aws-aiplc-rule-details/`; there is no `language/` (confirmed via the GitHub API).
    If our directive lived inside that tree, replacing the ruleset could not end at "swap
    out the whole directory" — doing that would delete the directive with it — and this
    product's top-priority constraint would hinge on human attention. And since that
    directory is treated as read-only, it was also a place we could not touch when the
    directive needed fixing.

    This check prevents going back: it fails if `language/` reappears in the ruleset tree.
    This invariant was a precondition for the move on 2026-08-21 to reference the upstream
    repo as the `steering-files/` submodule — that tree is now owned by upstream and is not
    a place for our content.
    """
    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "steering-files" / "aiplc-rules" / "language").exists()


def test_the_language_directive_is_code_not_a_file():
    """2026-08-19: replaced two files with two constants.

    The only production reader was `place_rules`, and all that being a file bought us was
    a "may be missing" state and a single raise guarding it. A constant cannot have that
    state.

    And it is this repo's existing convention for code to own model-facing text as two
    per-language editions — `agent/prompts.py`, `proto/prompts.py`, `survey/builder.py`, and
    `survey/report_labels.py` all take that form, and the `discovery_guard.py` header records
    it as the convention. `language/*.md` was the only exception.
    """
    from aipds.agent.workspace_rules import LANGUAGE_DIRECTIVES
    assert set(LANGUAGE_DIRECTIVES) == {"ko", "en"}
    assert all(v.strip() for v in LANGUAGE_DIRECTIVES.values())

    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "backend" / "aipds" / "agent" / "language").exists(), (
        "언어 지시가 다시 파일이 됐다 — 상수여야 '없는 상태'가 불가능하다")


def test_both_directives_reconcile_the_template_critical():
    """**Both editions** must reconcile the template's CRITICAL.

    The failure mechanism (7f33652) was: "the `**CRITICAL**: ... exactly as defined ... Do
    NOT deviate` right before the template was closer, so it beat the distant language
    directive." Unless that CRITICAL is called out by name, there is nothing linking it to
    our rule at the moment the agent reads `envision.md`.

    **The en edition needs it too.** The old en.md ended with "There is nothing to
    translate" and carried no such judgment at all — an English project still needs the
    decision "keep the structure, and put user-facing wording in this language" (literals in
    another language can creep into a template, and the distinction between structural
    markers and translatable text is language-independent).
    """
    for language in ("ko", "en"):
        text = _directive(language)
        assert "CRITICAL" in text, f"{language}: 양식의 CRITICAL을 지목하지 않는다"
        needle = "구조" if language == "ko" else "structure"
        assert needle in text, f"{language}: 구조와 언어를 구분하지 않는다"


def test_copies_rule_details_under_the_name_the_rules_expect(tmp_path):
    # If the name changed, every `./aws-aiplc-rule-details/common/...` read would break.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)))
    assert (ws / "aws-aiplc-rule-details" / "common" / "process-overview.md") \
        .read_text(encoding="utf-8") == "OVERVIEW"


def test_is_idempotent(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    first = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    place_rules(str(ws), str(rules), language="ko")
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == first


def test_skips_a_file_that_is_unchanged(tmp_path):
    """Do not rewrite dozens of files every turn — skip them if untouched.

    `copy2` preserves mtime, so after the first placement dst and src have the same
    (size, mtime), and the second call does nothing. Reverting to `copyfile` would update
    mtime every time and effectively turn this cache off — this check catches that.

    Note that the target is the detailed rules, not CLAUDE.md: CLAUDE.md is assembled, so it
    is written every time without comparison (if the two language directives happened to
    have the same fingerprint, switching languages would leave the file unchanged — exactly
    the silence this spec is meant to eliminate).
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    target = ws / "aws-aiplc-rule-details" / "common" / "process-overview.md"
    before = target.stat().st_mtime_ns
    place_rules(str(ws), str(rules), language="ko")
    assert target.stat().st_mtime_ns == before


def test_refreshes_a_rule_whose_size_did_not_change(tmp_path):
    """**Comparing only size would make a ruleset replacement silently half-apply.**

    The whole point of placing the rules every turn is to make a ruleset replacement reach
    projects in progress (rules are not in S3, and the workspace is rebuilt every turn —
    claude_driver._place_rules). But if the check were size only, an updated detailed rule
    whose byte count happened to match would stay stale on its own. It would show up only as
    the agent following an old procedure, so it would be nearly impossible to trace.

    The point is changing it to the **same length**, not `OVERVIEW` -> `REVISED!!`.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    target = ws / "aws-aiplc-rule-details" / "common" / "process-overview.md"
    assert target.read_text(encoding="utf-8") == "OVERVIEW"

    src = rules / "aws-aiplc-rule-details" / "common" / "process-overview.md"
    src.write_text("REVISED!", encoding="utf-8")      # length 8, same as the original
    assert src.stat().st_size == len("OVERVIEW")
    # Push mtime forward **explicitly**. The kernel's coarse clock updates only per tick
    # (even on a filesystem that distinguishes a measured 20ms gap), so if the placement above
    # and this write land in one tick, src's mtime stays the same — a situation possible only
    # inside a test; a ruleset replacement goes deploy -> restart -> next turn, which is always
    # farther apart than a tick. Here we deterministically reproduce "the deploy swapped the file".
    import os
    later = src.stat().st_mtime_ns + 1_000_000_000
    os.utime(src, ns=(later, later))

    place_rules(str(ws), str(rules), language="ko")
    assert target.read_text(encoding="utf-8") == "REVISED!"


def test_overwrites_a_file_whose_size_differs(tmp_path):
    # When the upstream rules are updated, the workspace must reflect it too.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    (ws / "CLAUDE.md").write_text("STALE", encoding="utf-8")
    place_rules(str(ws), str(rules), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# DISCOVERY PHASE WORKFLOW" in text and "STALE" not in text


def test_raises_when_core_workflow_is_missing(tmp_path):
    # If it silently proceeded without rules, the agent would run without knowing the
    # workflow — that shows up as an empty conversation and is hard to trace. Fail immediately.
    ws = tmp_path / "ws"
    ws.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        place_rules(str(ws), str(empty))


def test_works_against_the_real_repo_rules():
    # An integration pin that keeps the fixture from cementing a wrong layout while the real
    # placement breaks (same reason as test_file_read_reaches_real_rules_layout in test_agent_tools.py).
    import tempfile
    repo_rules = (Path(__file__).resolve().parents[2]
                  / "steering-files" / "aiplc-rules")
    if not (repo_rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        # steering-files/ is a submodule — it is empty if not initialized.
        pytest.skip("steering-files/ submodule not initialised")
    for language in ("ko", "en"):
        with tempfile.TemporaryDirectory() as ws:
            place_rules(ws, str(repo_rules), language=language)
            assert (Path(ws) / "CLAUDE.md").is_file()
            assert (Path(ws) / "aws-aiplc-rule-details" / "common").is_dir()


def test_core_workflow_has_no_language_directive_of_its_own():
    """The core invariant of this spec.

    If updating the upstream rules brought that line back, the conflict would silently
    return — in an English project, core-workflow's '한국어로 진행' ("proceed in Korean") and
    language/en.md would say opposite things, and which one wins is unpredictable (7f33652).
    """
    repo_rules = (Path(__file__).resolve().parents[2]
                  / "steering-files" / "aiplc-rules")
    core = repo_rules / "aws-aiplc-rules" / "core-workflow.md"
    if not core.is_file():
        # steering-files/ is a submodule — it is empty if not initialized.
        pytest.skip("steering-files/ submodule not initialised")
    text = core.read_text(encoding="utf-8")
    assert "한국어로 진행" not in text


def test_shared_config_dirs_have_no_language_directive():
    """The shared CLAUDE_CONFIG_DIR is shared by every project, so it cannot set the language.

    If left in, it would conflict with the workspace's language/en.md in English projects.
    """
    repo = Path(__file__).resolve().parents[2]
    for rel in ("discovery-config/CLAUDE.md", "proto-config/CLAUDE.md"):
        path = repo / rel
        if not path.is_file():
            pytest.skip(f"{rel} not present")
        text = path.read_text(encoding="utf-8")
        assert "한국어로 진행" not in text, rel
        # The translation override section must also have moved to language/ko.md.
        assert "번역해서 쓴다" not in text, rel
        # Deleting the two phrases is not enough: **the document's language is itself a
        # language signal** (the WHY comment at the top of these files, measured 2026-08-04 —
        # even after deleting the one-line directive, the English project's conversation kept
        # running in Korean). So we count characters. This check is blunt, but that is its
        # strength — any attempt to add a clause here in Korean gets caught.
        assert not {c for c in text if "가" <= c <= "힣"}, (
            f"{rel} 은 언어 중립이어야 한다 — 한글이 있으면 그 자체가 언어 신호다")


#: Anchors pointing at the writing-depth bar (shared config) and the language clause
#: (language directive) respectively. The two language files share no words (one is Korean
#: prose), so the only thing they can share is ASCII markers — the same method
#: test_proto_prompts uses to check the symmetry of two editions with shared tokens like "AskUserQuestion".
_DEPTH_BAR_MARKER = "<!-- depth-bar-items: derive, prose, unknowns, brackets, defaults -->"
_LANGUAGE_CLAUSE_MARKER = "<!-- depth-bar-language-clause -->"


def test_the_depth_bar_lives_in_the_shared_config_not_the_language_directives():
    """**The writing-depth bar lives as a single copy in the shared config.**

    Depth is language-neutral — the same bar applies whatever the language. Putting it in
    the language directive would make one Korean copy and one English copy, and two copies
    diverge: a regression that adds a clause to only one shows up with no error, only as a
    difference in artifact quality.

    The basis for this placement is the repo's precedent. When the upstream rules
    (aws-aiplc-rule-details) fall short, we do not edit them; we declare an override in
    discovery-config — the question file convention and the prototype build ban are the two
    precedents, and this is the third. Editing the upstream rules gets silently lost on
    resync (e12d806 -> 2047ac3 went through the same failure once with the language directive).

    The 2026-08-13 measurement is why this bar is needed: two sessions given the same input
    with only the language changed split at 20% vs 58% prose share (484 vs 3,823 characters),
    and **both passed the required-area completeness check.** Completeness is not the bar.
    """
    repo = Path(__file__).resolve().parents[2]
    config = repo / "discovery-config" / "CLAUDE.md"
    if not config.is_file():
        pytest.skip("discovery-config/CLAUDE.md not present")
    text = config.read_text(encoding="utf-8")
    assert _DEPTH_BAR_MARKER in text
    assert "overrides the upstream rules" in text
    # Prevents the items from disappearing while only the markers remain — the markers are the
    # list's table of contents, and if the contents and body diverge the check loses its meaning.
    body = text.split(_DEPTH_BAR_MARKER, 1)[1]
    assert body.count("\n- **") >= 5

    # It must not become two copies. If the same bar also went into the language directive,
    # drift would start and nobody could tell which one is current.
    for language in ("ko", "en"):
        assert _DEPTH_BAR_MARKER not in _directive(language), language


def test_both_language_directives_carry_the_length_calibration_clause():
    """The language-dependent half stays in the language directive.

    The model self-regulates length in **tokens**, and token cost differs by 3x across
    languages — the fact that a sense of "reasonable length" gives different results per
    language is part of the language convention. If this clause disappeared, no document
    would explain why the depth bar (shared config) is needed.
    """
    for language in ("ko", "en"):
        text = _directive(language)
        assert _LANGUAGE_CLAUSE_MARKER in text, language
        # It must point to where the depth bar is — without a sentence pointing to it, that
        # bar effectively does not exist for an agent that reads this document first.
        assert "Depth of what you write" in text, language


def test_upstream_question_rules_are_untouched():
    """The upstream rules are not edited — the question file convention is no exception.

    In AI-PDS the backend fills the `[Answer]:` slots (agent/question_file_answers.py), and
    the user cannot edit that file in the UI. So question-format-guide.md's "Missing Answers"
    (sending the user to that file) and Step 3 (waiting until the user says "done") do not fit
    this product — but deleting or editing that file is still forbidden. The upstream rules
    are data, and an update silently wipes local edits. Instead, discovery-config declares
    which side wins (the test below).
    """
    repo_rules = (Path(__file__).resolve().parents[2]
                  / "steering-files" / "aiplc-rules")
    guide = (repo_rules / "aws-aiplc-rule-details" / "common"
             / "question-format-guide.md")
    if not guide.is_file():
        # steering-files/ is a submodule — it is empty if not initialized.
        pytest.skip("steering-files/ submodule not initialised")
    text = guide.read_text(encoding="utf-8")
    # Two directives owned by upstream. AI-PDS neutralizes them not by editing the file but
    # by declaring an override in discovery-config.
    assert "If any [Answer]: tag is empty:" in text
    assert "#### Step 3: Wait for Confirmation" in text


def test_discovery_config_overrides_the_upstream_question_file_rules():
    """The answer write-back convention must be written in discovery-config.

    Without it, two rules apply to one situation and which one wins is unpredictable —
    upstream question-format-guide, on seeing an empty `[Answer]:`, tells the user to write
    the answer in that file (which cannot be edited in the UI) and to wait until they say
    "done" (the AskUserQuestion round trip already is the confirmation). That conflict has
    the same failure shape as 7f33652's duplicated language directive, so as with the
    prototype section, the document states which side wins.

    **Two things the agent must know are checked specifically.** The backend does the
    write-back, so (1) if the agent writes those slots itself, two writers fight over one
    line, and (2) the agent must know what the matching key is to keep it stable — both
    fail without errors, so if the document omits them nobody knows.

    On 2026-08-17 the key in (2) changed **from text to number**. Once questions were read
    straight from the file, there was no reason to match sentences with the tool call, and
    that "identical sentence" requirement was what had been flattening question files (15 of
    19 questions were a single paragraph with no background prose). A number only has to
    stay stable within the file, so the requirement is much weaker.
    """
    # Compare with whitespace collapsed — pin the rule's content, not where lines break
    # (the reasoning is in _discovery_config's docstring).
    text = _discovery_config()
    # Explicitly declared as an override (same wording as the prototype section's precedent).
    assert "overrides the upstream rules" in text
    assert "[Answer]:" in text
    # audit.md only changed role from source-of-truth record to audit trail; it is still required.
    assert "audit.md" in text
    # That the backend is the one doing the write-back.
    assert "Do not write them yourself" in text
    # That the matching key is the number — and that the tool is not used.
    assert "matched by question **number**" in text
    assert "AskUserQuestion is not available" in text


def test_the_turn_ending_writes_are_named_and_ordered():
    """**The 2026-08-18 defect is the reason for this check (test123456).**

    The PostToolUse hook ends the turn on certain file writes, and tool calls placed after
    it in the same message are not executed (claude_driver._on_post_tool_use). But this file
    did not state that; it only said "the turn ends once you finish writing the file". The
    measured result: the agent wrote audit.md -> the question file first and placed the
    welcome message, the Workspace Detection report, and the GATE notice **after** them, and
    none of it reached the screen. The same turn's `report_stage` was lost too, so
    aiplc-state.md was never created.

    All 3 runs with the same rules in Claude Code used the same order — locally there is no
    hook, so it simply went unpunished. In other words, unless the order is written down, an
    agent that follows the upstream rules faithfully loses on this path.

    The upstream rules already require this order: `core-workflow.md`'s "MANDATORY: Custom
    Welcome Message" and Workspace Detection step 6 ("Present completion message to user")
    come before the questioning stages. So this is not an override but **a statement that
    makes the upstream order hold on this path**.

    **It also pins that there are two turn-ending files.** At first it was only the question
    file; `build-instructions.md` joined on 2026-08-18 (the `handoff_prototype` tool moved
    into a hook — agent/reconcile.py). The list must live in one place so that when it
    becomes three, we do not dig the same pit again.

    And the opposing clause must not block this order. The old wording was "do not announce
    that you are about to ask", which read as "do not speak before writing" and collided head
    on with the "Keep the conversation visible" section — the measured turn skipping the
    welcome and leaving only two sentences is the shape of that collision.
    """
    text = _discovery_config()
    # The two turn-ending files are named together in one place.
    assert "Turn-ending writes" in text
    assert "build-instructions.md" in text
    assert "[Answer]:" in text
    # That calls placed after it are **discarded**. Without this, the order reads as a matter
    # of taste, and this failure arrives without an error.
    assert "discarded" in text
    # Name what comes first — given only the reason, the model improvises (prompts.py header).
    #
    # "submit_document" used to be in this list. That tool moved into the PostToolUse hook on
    # 2026-08-21 (agent/reconcile.document_events), so it is a **write**, not a call to name —
    # if an instruction to call a nonexistent tool remained, the model would waste the turn
    # looking for it.
    for before in ("document write", "audit.md", "aiplc-state.md"):
        assert before in text
    # Cite the upstream basis. Without it, the next person reads this clause as an AI-PDS
    # whim and deletes it on the next upstream resync.
    assert "core-workflow.md" in text
    # The conflicting wording does not come back. Only **transcribing** the questions should be forbidden.
    assert "do not announce that you are about to ask" not in text
    assert "Do not restate the questions in chat" in text


def test_the_state_file_is_the_agents_job_and_no_tool_is_named():
    """Pins that stage updates moved back from a tool to a file.

    Until 2026-08-18 this file overrode upstream with "call `report_stage` and do not write
    the state file yourself". When that tool was replaced by a hook (agent/reconcile.py),
    the basis for the override disappeared — the upstream rules originally require the
    agent to update this file directly (`common/workflow-changes.md`, each stage's "Update
    State Tracking"), and AI-PDS reads it. So this path now matches local Claude Code.

    **It must not say to call a tool that does not exist.** If the name remained, the agent
    would look for a nonexistent tool, and that failure is silent (it is not in the tool
    list, so the call never even happens).
    """
    text = _discovery_config()
    assert "report_stage" not in text
    assert "handoff_prototype" not in text
    # What to do fills that spot — if only the prohibition remained, the model would improvise.
    assert "aiplc-docs/aiplc-state.md" in text
    assert "Stage Progress" in text
    assert "workflow-changes.md" in text


def _discovery_config() -> str:
    """discovery-config/CLAUDE.md with whitespace collapsed.

    Why collapse line breaks: the sentences in this section are wrapped at 80 columns, so a
    raw substring check ends up checking **where lines break**. Then merely rewrapping a
    paragraph breaks the test (with the content unchanged), and conversely there is pressure
    to lay sentences out on one line to make checks pass. What we want to pin here is the
    rule's content.
    """
    return _folded_config("discovery-config")


def _proto_config() -> str:
    """proto-config/CLAUDE.md with whitespace collapsed — same reason as `_discovery_config`."""
    return _folded_config("proto-config")


def _folded_config(directory: str) -> str:
    path = Path(__file__).resolve().parents[2] / directory / "CLAUDE.md"
    if not path.is_file():
        pytest.skip(f"{directory}/CLAUDE.md not present")
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_prototype_scope_rule_is_a_boundary_not_a_command_list():
    """**The 2026-08-16 defect is the reason for this check.**

    The agent created `prototype/index.html` in the workspace. At the time, what this
    section prohibited was build *commands* — npm install / npm run dev / subprocesses /
    port selection. A single self-contained HTML file needs none of those, and the agent's
    own report ("no package installs or external communication needed") is the proof that it
    satisfied every clause.

    So the rule must be stated as a **boundary** (nothing outside aiplc-docs/). If we went
    back to listing commands, the next workaround would come in a different shape — a list
    invites the items it misses. The existence of an enforcement mechanism must be stated
    too: without knowing it, the agent mistakes a rejection for a bug and retries with only
    the path changed (see the agent/discovery_guard.py header).
    """
    text = _discovery_config()
    assert "You write only under `aiplc-docs/`" in text
    # That it is enforced + what a rejection tells you.
    assert "enforced, not trusted" in text


def test_both_prototype_layouts_are_documented_as_valid():
    """**Where the 2026-08-16 misjudgment was reversed.**

    At first we concluded "Path A.1 forgot the slug file" and instructed the agent to write
    it. That was wrong: the 7 artifacts `prototype-validation.md` declares are all under the
    singular `prototype/` (lines 556-562), and that document's only mention of `PROTOTYPE-`
    (line 16) is an entry condition: "if one already exists, go to build". A single
    prototype has nothing to distinguish, so there is nothing to become a slug.

    So the document must say **both layouts are legitimate**, and in particular must say not
    to create a slug copy — two copies of an 8KB spec drift, and the difference shows up with
    no error, only as a difference in content. Recognizing both layouts in card discovery is
    proto/layout.py's job (tests/test_proto_layout.py).
    """
    text = _discovery_config()
    # Where each of the two paths writes must be stated.
    assert "prototype/prototype-spec.md" in text
    assert "prototypes/{slug}/PROTOTYPE-{slug}.md" in text
    # No copies — this is the core of the reversed judgment.
    assert "Do not produce a slugged duplicate" in text
    # Record how the tree diagram's "All paths" should be read — that sentence was where the
    # misjudgment started, so the next person should not go down the same road.
    assert "governs a path beats the overview diagram" in text
    # On the path that uses a slug, the directory name == file name constraint still holds.
    assert "slug must equal the directory name" in text
    assert "lowercase letters, digits and" in text


#: An anchor confirming that the encoding section is **absent** from the assembled workspace CLAUDE.md.
_ENCODING_MARKER = "<!-- aipds-tool-encoding -->"


def test_the_assembled_claude_md_does_not_duplicate_the_encoding_rule(tmp_path):
    """**A check reversed on 2026-08-18.** It used to require this section to be present.

    The original basis (2026-08-16 ws-a-v3): when the model writes Hangul in tool parameters
    as `\\uXXXX` and mistypes the hex, it becomes a "valid but wrong" syllable — the file had
    `제공하시겠습니까` (U+ACA0) but the question asked was `제공하시겜습니까` (U+AC9C). The
    directive was already in `discovery-config/CLAUDE.md` and the defect happened anyway,
    because that file itself said "applies only to UI touchpoints" and sent the model to the
    working directory's CLAUDE.md. So we duplicated it there too.

    The basis disappeared on both sides:

    1. **That failure belonged to the AskUserQuestion path.** Files written with Write in
       the same turn were clean; only that tool's input was corrupted
       (agent/question_file_answers.py). That tool is now rejected by default
       (claude_driver.FILE_QUESTIONS_ENV).
    2. **The shared config stopped narrowing itself** — "applies to every tool call
       ... nothing below narrows it" is stated there, and
       `test_the_config_dir_does_not_scope_the_encoding_rule_away` below pins it.

    So we remove the duplicate. When one rule lives in two places, nobody can tell which is
    current — the same principle this file already pins for the depth bar
    (`test_the_depth_bar_lives_in_the_shared_config`).

    **We also confirm the rule itself did not disappear.** Removing the duplicate and losing
    the rule are one character apart in code, and the latter shows up only as corrupted
    Korean questions.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")

    assert _ENCODING_MARKER not in text
    folded = " ".join(text.split())
    assert "literal UTF-8" not in folded

    # **Also confirm the rule did not disappear entirely.** Removing the duplicate and losing
    # the rule are one character apart in code, and the latter shows up only as corrupted
    # Korean questions. The remaining location is `proto-config` — that path still requires
    # AskUserQuestion (full story in test_the_encoding_rule_survives_only_where_askuserquestion_does).
    proto = _proto_config()
    assert "literal UTF-8" in proto
    assert "\\uXXXX" in proto


def test_the_assembled_claude_md_starts_with_the_language_directive(tmp_path):
    """The language directive is **line 1**.

    It takes the spot vacated when the encoding section was removed. It is the only block
    that differs per project, and it has lost a fight before — in 7f33652 the CRITICAL of a
    template "closer in context" beat the language directive and some 20 PR/FAQ questions
    stayed in English. The premise of the whole document goes at the very front.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")

    assert text.startswith(_KO_MARK)


def test_the_encoding_rule_survives_only_where_askuserquestion_does():
    """**The asymmetry is intentional.** The two configs were made different on 2026-08-18.

    The encoding rule (claude-code#83033: `\\uXXXX` hex typo -> a different valid syllable)
    was a mitigation for one tool, AskUserQuestion. The two paths are in opposite situations:

    - **Discovery**: that tool is rejected (claude_driver.FILE_QUESTIONS_ENV, on by default).
      Questions go through files, and even if corrupted Hangul arrives, fuzzy matching
      records the answers (`_FUZZY_MIN` in agent/question_file_answers.py). The rule has
      nothing to do.
    - **Prototype build**: that tool is **required**. proto/prompts.py instructs the agent
      to get approval "after presenting the plan, always via AskUserQuestion", and
      proto/builder.py waits there. There is no question file, so there is no fuzzy matching
      behind it either.

    So we remove it on one side and keep it on the other. A cleanup aiming to make "the two
    shared configs the same shape" (132e409 went that way) will break this check, and what
    to read at that point is the removal-rationale comment at the top of
    discovery-config/CLAUDE.md.
    """
    proto = _proto_config()
    assert "literal" in proto and "uXXXX" in proto, (
        "프로토타입 경로는 AskUserQuestion이 필수다 — 규칙이 사라지면 승인 질문이 "
        "깨진 한글로 뜨고, 그쪽에는 퍼지 매칭이 없다")

    assert "as literal UTF-8" not in _discovery_config(), (
        "Discovery 경로에는 그 도구가 없다 — 규칙을 되살리면 죽은 완화책이 "
        "통합 계약의 맨 앞자리를 다시 차지한다")


def test_the_prototype_handoff_and_model_overrides_are_documented():
    """**Where two 2026-08-17 defects were closed with one section.**

    Path A.1's Step 3 is "Build Prototype", and upstream Steps 4-11 assume a running
    prototype. In AI-PDS the Prototypes tab does the build, but the document had only a
    prohibition with no stopping point or next action, so the agent improvised — measured on
    ws-a-v5: credential check -> API key request -> a list of 3 prerequisites, and zero
    mentions of the tab.

    The same gap showed up as a model problem too. `llm-model-configuration.md` requires a
    provider choice and an API key and writes three model IDs inconsistently, and there was
    no section neutralizing it. The project already has a model, and the build inherits it.

    **On 2026-08-18 the substitute action changed from a tool to a file.** `handoff_prototype`
    moved into the PostToolUse hook (agent/reconcile.py), so the stopping point is now writing
    `build-instructions.md`, not a tool call. What this check guards is unchanged: **the
    stopping point and the next action must be in the document.** Without them, the measured
    result was the improvisation above, and even with the tool turned into a hook, that gap
    comes back in the same shape.
    """
    text = _discovery_config()
    # The stopping point (writing what ends it) and the action after it must be there.
    assert "build-instructions.md" in text
    assert "that write is the handoff, and it ends your turn" in text.lower()
    # Steps 4-6 are deferred, not dropped — without saying so, the agent keeps going.
    assert "not abandoned; they are deferred" in text
    # Do not ask about models or credentials, and do not write model IDs into the spec.
    assert "already provisioned. Never ask for them" in text
    assert "do not write a model ID" in text


def test_the_config_says_where_a_document_preface_goes():
    """**Measured 2026-08-18 (ws-k).** The agent wrote "the nature of this document and
    how to answer" as a `##` section, so 1,100 characters of prose plus a table became
    **Question 1's elaboration** wholesale, and that heading was attached as the category of
    all 6 questions.

    It is not a parser defect — there is just one rule, which treats a `##` that is not
    `## Question` as a category and turns the prose under it into the next question's
    context (parsers/questions.py), and to that rule a document preamble and per-question
    elaboration look the same. The deciding factor, confirmed by measurement: **without** a
    `##` on the preamble, it becomes `preamble` as is and Question 1 is clean.

    So the only place we can fix this is telling the agent where things go. We do not touch
    upstream `question-format-guide.md` (it would be lost on resync) — this file's
    `overrides the upstream rules` is the place.

    We also record why we did not change the parser to "everything before the first
    question is preamble": there are real files where that shape is **legitimate** —
    `## 확인 1 — 숫자가 맞지 않습니다` in `pain-point-clarification-questions.md` and
    `## 제안한 프로토타입 범위 요약` in `design-context.md` are premises for the question right
    after them, and `## Positioning` in `strategy-questions.md` is a genuine category
    grouping Q1-Q3.
    """
    text = _discovery_config()
    assert "above the first `##` heading" in text
    # It must also say what happens if placed wrong — instructions without reasons are not
    # followed (the rule in the agent/prompts.py header).
    assert "becomes the *first question's* context" in text


def test_the_multi_select_marker_is_named():
    """**Measured (2026-08-21).** The question body said "(복수 선택 가능)" ("multiple
    selections allowed"), yet the screen showed a "하나만 선택" ("select one") badge and radio
    buttons, and the user worked around it by typing "A, B" into the `Other — 직접 입력`
    (Other — type your own) field — a structured answer was downgraded to free text, and the
    next stage cannot read it as option letters.

    Upstream `question-format-guide.md` has no concept of multi-select (it only defines
    single selection, `[Answer]: C`). So defining that convention is this file's job — the
    same division of labor as for the heading (`## Question N`), with the tolerance living in
    `parsers/questions.py`.

    **Why an instruction is needed.** The parser reads the parenthesized marker, so it works
    when the marker is present. But if whether to write the marker is left to the model's
    improvisation, the same question renders as a different widget each round — and that
    failure is silent (even with radio buttons, the screen looks normal).
    """
    text = _discovery_config()
    # Name the marker's exact form — given only the reason, the model improvises (prompts.py header).
    assert "복수 선택 가능" in text or "select all that apply" in text.lower(), (
        "discovery-config가 복수 선택 표시의 형태를 지목하지 않는다")
    # That the parentheses are the detection boundary. Without this, the model writes it as prose without parentheses and the parser misses it.
    assert "parenthes" in text.lower(), (
        "표시가 괄호 안에 와야 한다는 것을 적지 않았다 — 파서의 판정 경계다")
    # The answer format. The frontend joins letters with commas, and the write-back writes that value as is.
    assert "comma" in text.lower(), (
        "복수 선택 답변이 콤마로 이어진 letter라는 것을 적지 않았다")
