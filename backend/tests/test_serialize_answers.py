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
