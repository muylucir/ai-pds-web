# backend/aipds/agent/delta_buffer.py — lets token deltas through only up to a whitespace boundary.
#
# **Why it is needed.** With partial message streaming (`include_partial_messages`) on, assistant
# text arrives split into `text_delta`s of 4-5 characters on average (observed 2026-09-04: 1,325 characters →
# 295 pieces). Emitting those fragments as events as is breaks `_redacted` in `routes/turns.py`
# — it runs `redact_credentials` per event, and the patterns match whole
# strings (`AKIA[0-9A-Z]{12,}` etc.), so `AKIA` and the rest each fail to match.
# That is, **credentials silently go out to the client.** While text came in whole blocks it was safe
# by accident, and streaming removes that accident.
#
# **Why whitespace.** Those patterns all match only within tokens without whitespace
# (`\S+`, `[0-9A-Z]{12,}`, `[A-Za-z0-9\-]{10,}`, `[A-Za-z0-9\-]{4,}` …). A match cannot cross
# whitespace, so **a match within a fragment ending in whitespace is always contained entirely within
# that fragment.** So holding back only the whitespace-free tail restores redaction's original guarantee as is.
#
# **This module does not know about credentials.** All it knows is the single invariant "do not split
# whitespace-free tokens", and redaction stays owned by the route. The separation keeps the places to fix
# from becoming two when patterns grow — if a new pattern is also a whitespace-free token, this stays
# as is. If a pattern containing whitespace ever appears, that premise breaks, so
# noting that fact on the redaction.py side comes before modifying this module.
from __future__ import annotations

import re

#: The whitespace-free token at the end of the buffer. The position where this token starts is a safe cut point —
#: what precedes it ends in whitespace, so no pattern can grow across the boundary.
#:
#: Why `\Z` and not `$`: `$` is also true **before** a newline at the end of the string, so
#: for `"abc\n"` it computes the cut point as 0 and holds back a completed line.
_TRAILING_RUN = re.compile(r"\S*\Z")


class WhitespaceBoundaryBuffer:
    """Takes deltas, returns only the part ending in whitespace, and holds back the tail.

    Caller convention: an empty string from `feed` means **there is nothing to emit for this delta**,
    so no event is created. When the block ends (`content_block_stop`), the tail is taken with
    `flush` — that is the only point where it is certain the token will not grow further.
    Flushing midway would split a token that was actually continuing and break this invariant
    itself.
    """

    def __init__(self) -> None:
        self._held = ""

    def feed(self, chunk: str) -> str:
        """Adds a delta and returns the text that can be safely emitted now."""
        self._held += chunk
        cut = _TRAILING_RUN.search(self._held).start()  # type: ignore[union-attr]
        out, self._held = self._held[:cut], self._held[cut:]
        return out

    def flush(self) -> str:
        """Returns all of the held tail and clears it. Empty string if there is none."""
        out, self._held = self._held, ""
        return out
