# backend/tests/test_delta_buffer.py — 토큰 델타를 공백 경계까지만 흘리는 규칙.
#
# **왜 이 규칙인가.** 라우트의 레댁션(routes/turns.py의 `_redacted`)은 이벤트마다
# `redact_credentials`를 돌리고, 그 패턴 다섯 개는 모두 공백 없는 토큰 안에서만
# 매칭된다(`\S+`, `[0-9A-Z]{12,}`, `[A-Za-z0-9\-]{10,}`). 이벤트가 완성된 블록일
# 때는 그것으로 충분했지만, 토큰 델타로 쪼개면 `AKIA`와 뒷부분이 각각 매칭에
# 실패해 자격증명이 그대로 나간다. 공백 없는 꼬리를 붙들면 그 보장이 복원된다 —
# 매칭이 공백을 넘을 수 없으므로, 공백으로 끝나는 조각 안의 매칭은 항상 그 조각
# 안에 온전히 들어 있다.
from aipds.agent.delta_buffer import WhitespaceBoundaryBuffer


def test_holds_back_a_run_with_no_whitespace_yet():
    """공백을 아직 못 봤으면 내보낼 안전한 지점이 없다."""
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("AKIA") == ""


def test_emits_up_to_and_including_the_last_whitespace():
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("hello wor") == "hello "


def test_a_run_split_across_chunks_is_emitted_whole():
    """이것이 이 버퍼의 존재 이유다. 조각마다 레댁션이 돌아도 토큰이 쪼개지지
    않으므로 패턴이 온전한 문자열을 본다."""
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("AKIA") == ""
    assert buf.feed("IOSFODNN7EX") == ""
    assert buf.feed("AMPLE more") == "AKIAIOSFODNN7EXAMPLE "


def test_newline_is_a_boundary():
    """코드 블록과 문단은 공백 문자가 개행뿐인 경우가 많다."""
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("first\nsecond") == "first\n"


def test_flush_releases_the_held_tail():
    buf = WhitespaceBoundaryBuffer()
    buf.feed("tail-without-space")
    assert buf.flush() == "tail-without-space"


def test_flush_empties_the_buffer():
    """블록이 끝날 때 부르므로, 다음 블록이 앞 블록의 꼬리를 물려받으면 안 된다."""
    buf = WhitespaceBoundaryBuffer()
    buf.feed("x")
    buf.flush()
    assert buf.flush() == ""


def test_flush_on_empty_buffer_is_empty_string():
    """호출부가 "내보낼 것이 있는가"를 값의 유무로 판단한다."""
    assert WhitespaceBoundaryBuffer().flush() == ""


def test_emitted_text_concatenates_back_to_the_input():
    """조각을 이어 붙이면 입력과 같아야 한다 — 한 글자도 잃거나 더하지 않는다."""
    chunks = ["Hel", "lo ", "wor", "ld\nAKIA", "IOSFODNN7EXAMPLE", " end"]
    buf = WhitespaceBoundaryBuffer()
    out = "".join(buf.feed(c) for c in chunks) + buf.flush()
    assert out == "".join(chunks)
