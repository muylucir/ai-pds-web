# backend/tests/test_delta_buffer.py — the rule of streaming token deltas only up to a whitespace boundary.
#
# **Why this rule.** The route's redaction (`_redacted` in routes/turns.py) runs
# `redact_credentials` on every event, and all five of its patterns match only within tokens without
# whitespace (`\S+`, `[0-9A-Z]{12,}`, `[A-Za-z0-9\-]{10,}`). When events were complete blocks that was
# enough, but split into token deltas, `AKIA` and the rest would each fail to match and the credential
# would go out as is. Holding back the whitespace-free tail restores that guarantee — since a match
# cannot cross whitespace, any match within a piece ending in whitespace is always contained entirely
# within that piece.
from aipds.agent.delta_buffer import WhitespaceBoundaryBuffer


def test_holds_back_a_run_with_no_whitespace_yet():
    """If no whitespace has been seen yet, there is no safe point to emit at."""
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("AKIA") == ""


def test_emits_up_to_and_including_the_last_whitespace():
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("hello wor") == "hello "


def test_a_run_split_across_chunks_is_emitted_whole():
    """This is the reason this buffer exists. Even with redaction running per piece, tokens are not
    split, so the patterns see whole strings."""
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("AKIA") == ""
    assert buf.feed("IOSFODNN7EX") == ""
    assert buf.feed("AMPLE more") == "AKIAIOSFODNN7EXAMPLE "


def test_newline_is_a_boundary():
    """Code blocks and paragraphs often have newlines as their only whitespace."""
    buf = WhitespaceBoundaryBuffer()
    assert buf.feed("first\nsecond") == "first\n"


def test_flush_releases_the_held_tail():
    buf = WhitespaceBoundaryBuffer()
    buf.feed("tail-without-space")
    assert buf.flush() == "tail-without-space"


def test_flush_empties_the_buffer():
    """It is called when a block ends, so the next block must not inherit the previous block's tail."""
    buf = WhitespaceBoundaryBuffer()
    buf.feed("x")
    buf.flush()
    assert buf.flush() == ""


def test_flush_on_empty_buffer_is_empty_string():
    """The caller decides "is there something to emit" by whether a value is present."""
    assert WhitespaceBoundaryBuffer().flush() == ""


def test_emitted_text_concatenates_back_to_the_input():
    """Concatenating the pieces must equal the input — not a single character lost or added."""
    chunks = ["Hel", "lo ", "wor", "ld\nAKIA", "IOSFODNN7EXAMPLE", " end"]
    buf = WhitespaceBoundaryBuffer()
    out = "".join(buf.feed(c) for c in chunks) + buf.flush()
    assert out == "".join(chunks)
