# backend/tests/test_parser_corpus.py — runs every parser over the observed corpus.
#
# The corpus is a shuffled mix of real agent artifacts from the drill buckets (tests/parser_corpus.py
# header). Two kinds of things are asserted here.
#
# 1. **Invariants** — "what should be read is read." These assertions still apply when new file
#    shapes appear: fail if the audit log has 0 entries, fail if sub-steps get mixed into stages,
#    fail if option lines present in the file are missing from the questions.
# 2. **Snapshots** — it reads into the same structure as `summary.json`. If fixing a parser changes the
#    result, check whether the change is intended and regenerate with `parser_corpus.py`.
#
# When upgrading the model, the rule set (steering-files submodule), or the SDK, collect new artifacts,
# refresh the corpus, and run this test — this is where shapes the parser does not know are caught before
# they silently become empty screens.
import json
import re
from pathlib import Path

import pytest

from aipds.parsers.audit import parse_audit_file
from aipds.parsers.questions import parse_question_file
from aipds.parsers.state import parse_state_file
from parser_corpus import CORPUS, kind_of, summarize

_SUMMARY = json.loads((CORPUS / "summary.json").read_text(encoding="utf-8"))
_FILES = sorted(p for p in CORPUS.rglob("*.md"))


def _rel(path: Path) -> str:
    return path.relative_to(CORPUS).as_posix()


def _of(kind: str) -> list[Path]:
    return [p for p in _FILES if kind_of(p) == kind]


def test_the_corpus_covers_both_languages_and_every_parser():
    langs = {_rel(p).split("-")[0] for p in _FILES}
    assert langs == {"ko", "en"}
    assert _of("audit") and _of("state") and _of("questions")
    assert set(_SUMMARY) == {_rel(p) for p in _FILES}


@pytest.mark.parametrize("path", _FILES, ids=_rel)
def test_parses_to_the_recorded_structure(path):
    md = path.read_text(encoding="utf-8")
    assert summarize(kind_of(path), path.name, md) == _SUMMARY[_rel(path)]


# ---- Audit log ----

@pytest.mark.parametrize("path", _of("audit"), ids=_rel)
def test_every_logged_interaction_becomes_an_entry(path):
    """**Most** headings must be entries. The count can drop by as much as sub-sections (`## Question N`) attach
    to the preceding entry, but below half it is a shape the parser does not know."""
    md = path.read_text(encoding="utf-8")
    entries = parse_audit_file(md)
    stamped = len(re.findall(r"^## \d{4}-\d{2}-\d{2}T", md, re.M))
    headings = len(re.findall(r"^## ", md, re.M))
    assert entries, "no entries — the review panels would be empty"
    assert len(entries) >= max(stamped, headings // 2)


@pytest.mark.parametrize("path", _of("audit"), ids=_rel)
def test_every_entry_has_a_time_and_an_ai_side(path):
    for e in parse_audit_file(path.read_text(encoding="utf-8")):
        assert e.timestamp, f"entry {e.index} ({e.context}) has no timestamp"
        assert e.ai_response.strip(), f"entry {e.index} ({e.context}) has no AI side"


@pytest.mark.parametrize("path", _of("audit"), ids=_rel)
def test_most_entries_carry_the_users_words(path):
    """Entries where only the AI acted (workspace detection, writing question files) correctly have no input.
    Still, the majority of the conversation is user utterances — below that, the input label is being missed."""
    entries = parse_audit_file(path.read_text(encoding="utf-8"))
    with_input = [e for e in entries if e.user_input.strip()]
    assert len(with_input) * 2 >= len(entries)


# ---- State file ----

@pytest.mark.parametrize("path", _of("state"), ids=_rel)
def test_sub_steps_are_not_stages(path):
    state = parse_state_file(path.read_text(encoding="utf-8"))
    assert state.stages
    for s in state.stages:
        assert not re.match(r"Step\s*\d", s.name), s.name
        # The status marker is on the memo side — the name is the key.
        assert "✅" not in s.name, s.name
    assert len(state.stages) <= 12


# ---- Question files ----

@pytest.mark.parametrize("path", _of("questions"), ids=_rel)
def test_no_option_line_is_dropped(path):
    """The number of option lines in the file must equal the number of options the questions have — options
    with letters the parser does not know are **silently** dropped from the card."""
    md = path.read_text(encoding="utf-8")
    qfile = parse_question_file(path.name, md)
    assert qfile.parse_ok
    in_file = len(re.findall(r"^[A-Z]\)\s", md, re.M))
    assert sum(len(q.options) for q in qfile.questions) == in_file
