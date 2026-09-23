# backend/tests/test_parser_corpus.py — 모든 파서를 실측 코퍼스에 돌린다.
#
# 코퍼스는 드릴 버킷의 실제 에이전트 산출물을 뒤섞은 것이다(tests/parser_corpus.py
# 헤더). 여기서 단정하는 것은 두 종류다.
#
# 1. **불변식** — "읽혀야 할 것이 읽힌다." 파일 모양이 새로 나타나도 이 단정은 그대로
#    적용된다: 감사 로그의 항목이 0건이면 실패, 스테이지에 하위 단계가 섞이면 실패,
#    파일에 있는 보기 줄이 문항에서 빠지면 실패.
# 2. **스냅샷** — `summary.json`과 같은 구조로 읽힌다. 파서를 고쳐 결과가 바뀌면
#    의도한 변화인지 보고 `parser_corpus.py`로 다시 만든다.
#
# 모델·룰셋(steering-files 서브모듈)·SDK를 올리면 새 산출물을 받아 코퍼스를
# 갱신하고 이 테스트를 돌린다 — 파서가 모르는 모양이 조용히 빈 화면이 되는 것을 여기서
# 잡는다.
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


# ---- 감사 로그 ----

@pytest.mark.parametrize("path", _of("audit"), ids=_rel)
def test_every_logged_interaction_becomes_an_entry(path):
    """헤딩 **대부분**이 항목이어야 한다. 하위 절(`## Question N`)이 앞 항목에 붙는
    만큼 줄어들 수는 있지만, 절반 밑이면 파서가 모르는 모양이다."""
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
    """AI만 움직인 항목(워크스페이스 감지, 질문 파일 작성)은 입력이 없는 것이 맞다.
    그래도 대화의 과반은 사용자 발화다 — 그 밑이면 입력 라벨을 놓치고 있다."""
    entries = parse_audit_file(path.read_text(encoding="utf-8"))
    with_input = [e for e in entries if e.user_input.strip()]
    assert len(with_input) * 2 >= len(entries)


# ---- 상태 파일 ----

@pytest.mark.parametrize("path", _of("state"), ids=_rel)
def test_sub_steps_are_not_stages(path):
    state = parse_state_file(path.read_text(encoding="utf-8"))
    assert state.stages
    for s in state.stages:
        assert not re.match(r"Step\s*\d", s.name), s.name
        # 상태 표시는 메모 쪽이다 — 이름은 키다.
        assert "✅" not in s.name, s.name
    assert len(state.stages) <= 12


# ---- 질문 파일 ----

@pytest.mark.parametrize("path", _of("questions"), ids=_rel)
def test_no_option_line_is_dropped(path):
    """파일에 있는 보기 줄의 수와 문항들이 가진 보기의 수가 같아야 한다 — 파서가
    모르는 글자의 보기는 카드에서 **조용히** 빠진다."""
    md = path.read_text(encoding="utf-8")
    qfile = parse_question_file(path.name, md)
    assert qfile.parse_ok
    in_file = len(re.findall(r"^[A-Z]\)\s", md, re.M))
    assert sum(len(q.options) for q in qfile.questions) == in_file
