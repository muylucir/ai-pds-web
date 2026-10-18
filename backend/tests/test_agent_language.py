# backend/tests/test_agent_language.py
#
# This file guards a single invariant: **an English project's agent context contains
# no Hangul.**
#
# Why a new file. The tests for spec 2026-08-03-bilingual-ko-en
# (test_workspace_rules.py) verified "there is one language directive and it comes first",
# and all of them passed. Yet on 2026-08-04 the workspace chat of a project that chose
# English ran in Korean. Those tests missed it because what they checked was the
# **directive** — the leaks were in text that is not a directive:
#
#   1. The full CLAUDE.md in the shared CLAUDE_CONFIG_DIR (1,935 characters of Korean
#      prose). It is shared by all projects so it cannot carry a language, yet it was
# Korean.
#   2. MCP tool descriptions — the model reads them with the tool list every turn.
#   3. Tool return strings and the model-facing prompts the driver builds.
#
# So here we look at **characters**, not assembly order. A single Hangul code point means
# failure. The check is blunt, but that is exactly its strength — whoever next leaks Korean
# through whatever new channel, that channel has to take a language to pass this check.
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aipds.agent import prompts
from aipds.agent.workspace_rules import place_rules
from aipds.proto import prompts as proto_prompts
from aipds.proto.tools import build_proto_tools


REPO = Path(__file__).resolve().parents[2]


def hangul(text: str) -> list[str]:
    """Return the Hangul syllables in the text, deduplicated (empty list = no Hangul).

    Why jamo (ㄱ-ㅣ) are not included: unlike arrows or symbols, real prose contains only
    precomposed syllables. Checking precomposed syllables alone does not miss a single line
    of prose.
    """
    return sorted({c for c in text if "가" <= c <= "힣"})


# ---- ① Workspace CLAUDE.md (what place_rules assembles) ----


def test_english_workspace_rules_have_no_korean():
    """The full assembled workspace CLAUDE.md must contain no Hangul.

    test_workspace_rules.py only checks "there is no KO-DIRECTIVE" (its fixtures are
    placeholders, so it cannot check more). Here we assemble with the real repo rules and
    count characters — this also catches Korean creeping into the upstream rules or
    language/en.md.
    """
    rules = REPO / "steering-files" / "aiplc-rules"
    if not (rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        # steering-files/ is a submodule — in an uninitialized tree it is empty,
        # and this check is then silently skipped. `git submodule update --init`.
        pytest.skip("steering-files/ submodule not initialised")
    with tempfile.TemporaryDirectory() as ws:
        place_rules(ws, str(rules), language="en")
        text = (Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
    assert not hangul(text), (
        f"영어 프로젝트의 워크스페이스 CLAUDE.md에 한글이 있다: {hangul(text)[:12]}")


def test_korean_workspace_rules_still_carry_the_korean_directive():
    """Symmetry check — prevents a regression that empties ko while fixing en."""
    rules = REPO / "steering-files" / "aiplc-rules"
    if not (rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        # steering-files/ is a submodule — in an uninitialized tree it is empty,
        # and this check is then silently skipped. `git submodule update --init`.
        pytest.skip("steering-files/ submodule not initialised")
    with tempfile.TemporaryDirectory() as ws:
        place_rules(ws, str(rules), language="ko")
        text = (Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
    assert "한국어로 진행" in text


# ---- ② Shared CLAUDE_CONFIG_DIR (the main cause of this defect) ----


@pytest.mark.parametrize("config_dir", ["discovery-config", "proto-config"])
def test_shared_config_dir_claude_md_is_language_neutral(config_dir):
    """**This was the main cause of this defect.**

    Both directories are the "user" level of setting_sources and shared by all projects —
    they cannot carry a per-project language. Spec §3 removed the *one-line* language
    directive from here, but the document body remained Korean prose. To the model that is
    itself a language signal: an English project's agent read 1,935 characters of Korean
    every turn, competing with the workspace's "conduct all conversation in English".

    That is why we look at characters rather than the presence of a directive. A change
    that reverts this file to Korean cannot pass this test.

    **proto-config joined this assertion late (2026-08-13).** While only discovery was
    checked, proto-config/CLAUDE.md stayed with 1,807 of its 5,283 characters in Hangul
    — same structure, same failure path, but guarding only one side lets the other quietly
    regress. The build agent's project language comes from proto/prompts.py (complete in
    both
    languages) and the workspace CLAUDE.md, and only those two can differ per project.
    """
    path = REPO / config_dir / "CLAUDE.md"
    if not path.is_file():
        pytest.skip(f"{config_dir}/CLAUDE.md not present")
    text = path.read_text(encoding="utf-8")
    assert not hangul(text), (
        f"공유 config dir({config_dir})의 CLAUDE.md는 전 프로젝트가 읽으므로 "
        f"언어 중립이어야 한다. 한글 {len(hangul(text))}자 발견: {hangul(text)[:12]}")


# ---- ③ MCP tool descriptions and return strings (prompts the model reads) ----
#
# **The target moved from Discovery to the prototype builder (2026-08-21).** The checks here
# used to look at `build_tools` in `agent/tools.py`, but that module's only tool
# (`submit_document`) was replaced by a PostToolUse hook and the whole module went away. The
# remaining custom tool is the builder's `build_complete`, and it has the same failure path
# —
# a tool description is a prompt the model reads with the tool list every turn. So the
# checks
# are moved rather than deleted (the same discipline as moving the report_stage check to
# test_agent_reconcile.py).


def _proto_tools(language: str | None = None):
    if language is None:
        return build_proto_tools("/tmp/ws", lambda e: None)
    return build_proto_tools("/tmp/ws", lambda e: None, language)


def test_english_tool_descriptions_have_no_korean():
    """Tool descriptions are prompts that enter the model context every turn."""
    bad = {t.name: t.description for t in _proto_tools("en")
           if hangul(t.description)}
    assert not bad, f"영어 프로젝트의 도구 설명에 한글이 있다: {bad}"


def test_korean_tool_descriptions_are_still_korean():
    """Symmetry check — wiring English must not lose Korean."""
    descs = [t.description for t in _proto_tools("ko")]
    # If empty, all() would be true and this check would silently become meaningless.
    assert descs, "커스텀 도구가 하나도 없다 — 이 검사가 공허해졌다"
    assert all(hangul(d) for d in descs), descs


def test_build_proto_tools_defaults_to_korean():
    """Callers that pass no argument (old code, tests) keep the existing behavior."""
    descs = [t.description for t in _proto_tools()]
    assert descs, "커스텀 도구가 하나도 없다 — 이 검사가 공허해졌다"
    assert all(hangul(d) for d in descs), descs


async def test_build_complete_refusals_follow_the_project_language(tmp_path):
    """Refusal strings are instructions the agent reads to fix itself — they must match the
    conversation language.

    `test_submit_document_refusals_follow_the_project_language` used to be here. That
    tool is gone, so the same thing is checked on a tool with the same contract: if
    completion
    is declared without an artifact, the user sees the "빌드 완료" (build complete) card but
    there is nothing to host.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    tools = {t.name: t for t in build_proto_tools(str(ws), lambda e: None, "en")}
    out = (await tools["build_complete"].handler(
        {"summary": "done"}))["content"][0]["text"]
    assert not hangul(out), out


# ---- ④ Model- and user-facing text the driver builds ----


@pytest.mark.parametrize("fn,kwargs", [
    (prompts.answer_first, {}),
    (prompts.turn_failed, {}),
    (prompts.question_payload_rejected, {"reason": "no options"}),
    # summary/path are quoted values — give them Hangul-free values to check only the
    # **wrapper's language**. The real body quotes sentences from the question file, so it
    # follows the project language (aipds/answer_summary.py, 2026-08-21).
    (prompts.file_answers_recorded, {"path": "a.md", "summary": "Q1.\n-> A"}),
    (prompts.state_file_missing, {}),
    (prompts.prototype_handoff_stop, {"slug": "prototype"}),
    (proto_prompts.design_rules, {}),
    (proto_prompts.build_complete_theme_rejection, {}),
    (proto_prompts.unsafe_command_refused, {"fragment": "npm run dev"}),
])
def test_every_english_prompt_has_no_korean(fn, kwargs):
    """Sweep every English branch in prompts.py exhaustively.

    Adding a new function while omitting its en branch is how this defect recurs, so this
    checks the module itself rather than individual call sites.
    """
    out = fn("en", **kwargs)
    assert not hangul(out), f"{fn.__name__}('en')에 한글: {out}"
    assert out.strip(), f"{fn.__name__}('en')이 비었다"


@pytest.mark.parametrize("fn,kwargs", [
    (prompts.answer_first, {}),
    (prompts.turn_failed, {}),
    (prompts.question_payload_rejected, {"reason": "옵션 없음"}),
    (prompts.file_answers_recorded, {"path": "a.md", "summary": "Q1.\n-> A"}),
    (prompts.state_file_missing, {}),
    (prompts.prototype_handoff_stop, {"slug": "prototype"}),
    (proto_prompts.design_rules, {}),
    (proto_prompts.build_complete_theme_rejection, {}),
    (proto_prompts.unsafe_command_refused, {"fragment": "npm run dev"}),
])
def test_every_korean_prompt_is_korean_and_unknown_falls_back(fn, kwargs):
    """The ko branch is alive, and an arbitrary string from a corrupted manifest falls back
    to ko
    (the same discipline as place_rules — better than running without a prompt)."""
    assert hangul(fn("ko", **kwargs)), fn.__name__
    assert fn("klingon", **kwargs) == fn("ko", **kwargs), fn.__name__


def test_answers_resumed_carries_the_record_in_both_languages():
    """Answer-delivery prompt after a restart. `record` is the only persistent trace of
    which
    question round the answers belong to (S3 pending is deleted on the way out), so both
    languages must carry it."""
    record = '{"interrupt_id": "i-1"}'
    lines = "- Q → A"
    en = prompts.answers_resumed("en", lines, record)
    ko = prompts.answers_resumed("ko", lines, record)
    for out in (en, ko):
        assert record in out and lines in out
    assert not hangul(en), en
    assert hangul(ko), ko


# `test_submit_document_description_keeps_the_write_first_order_in_both` used to be
# here. The core of that sentence was the ordering instruction ("write first, then call"),
# and with the tool gone there is no order left to keep — the signal is now derived from
# the write, so the order cannot be inverted.


# ---- ⑤ Driver wiring ----
#
# `test_driver_passes_its_language_to_the_tools` used to be here. When Discovery's custom
# tool (`submit_document`) went away on 2026-08-21 there was nothing left to check —
# Discovery
# wires no custom tools at all, so language cannot leak through that channel
# (the `mcp_servers` comment in claude_driver). The remaining tools are the builder's, and ③
# above covers them.
