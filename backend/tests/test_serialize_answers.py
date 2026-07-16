# backend/tests/test_serialize_answers.py
from pathlib import Path
from pathfinder.parsers.questions import serialize_answers, parse_question_file

FIX = Path(__file__).parent / "fixtures"

def test_writes_answers_and_reparses():
    md = (FIX / "discovery-mode-selection-questions.md").read_text(encoding="utf-8")
    out = serialize_answers(md, {1: "B"})
    assert "[Answer]: B" in out
    assert parse_question_file("x.md", out).questions[0].answer == "B"

def test_multiselect_value_written():
    md = (FIX / "strategy-questions.md").read_text(encoding="utf-8")
    out = serialize_answers(md, {12: "A,C"})
    reparsed = {q.number: q.answer for q in parse_question_file("x.md", out).questions}
    assert reparsed[12] == "A,C"
    # untouched question retains original answer
    assert reparsed[1] == "A"

def test_unknown_question_number_raises():
    md = (FIX / "discovery-mode-selection-questions.md").read_text(encoding="utf-8")
    try:
        serialize_answers(md, {99: "A"})
        assert False, "expected KeyError"
    except KeyError:
        pass

def test_preserves_exact_bytes_of_untargeted_lines():
    md = (FIX / "strategy-questions.md").read_text(encoding="utf-8")
    out = serialize_answers(md, {1: "A"})  # Q1's answer is already "A" — rewriting to same value
    # The fixture's answer line is written as "[Answer]:A" (no space), while
    # serialize_answers always emits "[Answer]: <value>" (with a space), so the
    # rewritten line itself is NOT byte-identical even though the value is
    # unchanged. Every other line must be byte-for-byte identical.
    md_lines = md.splitlines(keepends=True)
    out_lines = out.splitlines(keepends=True)
    assert len(md_lines) == len(out_lines)
    diffs = [i for i, (a, b) in enumerate(zip(md_lines, out_lines)) if a != b]
    assert diffs == [14], f"unexpected differing lines: {diffs}"
    assert md_lines[14] == "[Answer]:A\n"
    assert out_lines[14] == "[Answer]: A\n"

def test_preserves_crlf_line_endings():
    md = "## Question 1\nPick one\nA) x\nX) Other\n[Answer]: A\n".replace("\n", "\r\n")
    out = serialize_answers(md, {1: "B"})
    assert "\r\n" in out
    assert "[Answer]: B\r\n" in out
    # no bare LF introduced on the rewritten line
    assert "[Answer]: B\n" not in out.replace("\r\n", "")
