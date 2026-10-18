# backend/aipds/agent/tool_input_draft.py — the "so far" of the file the model is writing.
#
# **Why it exists (measured 2026-10-07).** About 60% of Discovery turn time was spent generating
# file contents (1029 of 1709 seconds for ws-c, 964 of 1649 seconds for ws-b). Executing the write
# tool takes 0.1 seconds each time — what takes time is the model streaming the tool input (the full
# file), and for Part 1 of `discovery-document.md` that alone was 128–136 seconds. Meanwhile the screen
# showed no signal: the driver sent only text deltas to the screen and learned of tool blocks only once complete.
# The moment of writing the most important document was the longest silence, and the case where a user
# cut off the turn 6 seconds after seeing the guidance sentence (ws-e) is what that looks like.
#
# Bedrock sends tool input in small pieces as `input_json_delta` (measured: 583 pieces for one Korean
# document, evenly over 40 seconds). Those pieces break **in the middle of a JSON string**, so they
# cannot be read as is — this module decodes them to obtain `file_path` and the body length bit by bit.
#
# **Only the path and character count go on screen.** "문서 작성 중 · business-context.md · 1,234자"
# is enough to judge whether it is worth waiting — the character count going up is proof of life. We
# judged that previewing the body in real time is not needed (2026-10-07). Since the body is not carried,
# there is also no path for the contents of files like the audit log, whose credentials are scrubbed when uploaded to the source of truth, to leak in real time.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from aipds.models import AgentEvent

#: Event kind. Must be the same value as the Literal in `models.AgentEvent.kind`.
DRAFT = "draft"

#: Minimum interval (seconds) between events for the same draft. Pieces arrive every few tens of ms, so
#: passing them all through means hundreds per document — at this interval the screen reads it well enough as "being written".
EMIT_INTERVAL_S = 0.25

#: Top-level keys to decode. Other values (MultiEdit's `edits` array, etc.) are skipped.
_PATH_KEY = "file_path"
_BODY_KEYS = {"Write": "content", "Edit": "new_string"}

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "\\": "\\", "/": "/"}


class JsonStringFields:
    """Decode top-level string fields bit by bit from a streaming JSON object.

    It is not a general JSON parser. Tool input is a flat object, and all we need is its top-level
    string values. Nested values are skipped, counting only depth. Even if an escape is cut at a
    piece boundary (after `\\`, in the middle of the four `\\u` digits, between a surrogate pair), it continues with the next piece.
    """

    def __init__(self, keys: set[str]):
        self._keys = keys
        self._depth = 0
        self._expect_key = False
        self._in_string = False
        self._role = "skip"          # "key" | "value" | "skip"
        self._key = ""               # key being read now (role == "key")
        self._last_key = ""          # the key that just ended — the name of the value that follows
        self._value_key: str | None = None
        self._escape: str | None = None   # None | "\\" | "u" + hex so far
        self._high: int | None = None     # high surrogate waiting for its pair
        self.values: dict[str, str] = {}
        self.complete: set[str] = set()

    def feed(self, chunk: str) -> dict[str, str]:
        """Feed a piece, and return the text newly decoded this time, per key."""
        fresh: dict[str, str] = {}
        for ch in chunk:
            if self._in_string:
                self._string_char(ch, fresh)
            else:
                self._structure_char(ch)
        return fresh

    def _structure_char(self, ch: str) -> None:
        if ch == '"':
            self._in_string = True
            if self._depth == 1 and self._expect_key:
                self._role, self._key = "key", ""
            elif self._depth == 1 and self._last_key in self._keys:
                self._role, self._value_key = "value", self._last_key
                self.values.setdefault(self._last_key, "")
            else:
                self._role = "skip"
        elif ch in "{[":
            self._depth += 1
            if self._depth == 1:
                self._expect_key = True
        elif ch in "}]":
            self._depth -= 1
        elif self._depth == 1 and ch == ":":
            self._expect_key = False
        elif self._depth == 1 and ch == ",":
            self._expect_key = True

    def _string_char(self, ch: str, fresh: dict[str, str]) -> None:
        if self._escape is None:
            if ch == "\\":
                self._escape = "\\"
            elif ch == '"':
                self._end_string()
            else:
                self._emit(ch, fresh)
            return
        if self._escape == "\\":
            if ch == "u":
                self._escape = "u"
            else:
                self._escape = None
                self._emit(_ESCAPES.get(ch, ch), fresh)
            return
        self._escape += ch
        if len(self._escape) < 5:      # "u" + four digits
            return
        code, self._escape = int(self._escape[1:], 16), None
        if 0xD800 <= code <= 0xDBFF:
            self._high = code
            return
        if 0xDC00 <= code <= 0xDFFF and self._high is not None:
            code = 0x10000 + ((self._high - 0xD800) << 10) + (code - 0xDC00)
            self._high = None
        self._emit(chr(code), fresh)

    def _emit(self, text: str, fresh: dict[str, str]) -> None:
        if self._high is not None:     # unpaired high surrogate — shown rather than discarded
            self._high = None
            text = "\ufffd" + text
        if self._role == "key":
            self._key += text
        elif self._role == "value" and self._value_key is not None:
            self.values[self._value_key] += text
            fresh[self._value_key] = fresh.get(self._value_key, "") + text

    def _end_string(self) -> None:
        self._in_string = False
        if self._role == "key":
            self._last_key = self._key
        elif self._role == "value" and self._value_key is not None:
            self.complete.add(self._value_key)
            self._value_key = None
        self._role = "skip"


@dataclass
class ToolInputDraft:
    """One write tool block. The driver creates it at block start and calls `feed` per piece.

    `to_rel` converts the absolute path in the tool input to a workspace-relative path (None if
    outside) — it is the driver's `_rel`.
    """
    tool_use_id: str
    tool: str
    to_rel: Callable[[str], str | None]
    path: str | None = None
    chars: int = 0
    _fields: JsonStringFields = field(init=False)
    _body_key: str | None = field(init=False)
    _last_emit: float | None = field(default=None, init=False)
    _emitted_chars: int = field(default=-1, init=False)

    def __post_init__(self) -> None:
        self._body_key = _BODY_KEYS.get(self.tool)
        keys = {_PATH_KEY} | ({self._body_key} if self._body_key else set())
        self._fields = JsonStringFields(keys)

    def start(self, now: float) -> list[AgentEvent]:
        """The block opened. Announce "started writing" right away, even without knowing what is being written."""
        return [self._event("writing", now)]

    def feed(self, partial_json: str, now: float) -> list[AgentEvent]:
        fresh = self._fields.feed(partial_json)
        if self._body_key and self._body_key in fresh:
            self.chars += len(fresh[self._body_key])
        announced = False
        if self.path is None and _PATH_KEY in self._fields.complete:
            self.path = self.to_rel(self._fields.values[_PATH_KEY])
            announced = True
        due = (self._last_emit is None or now - self._last_emit >= EMIT_INTERVAL_S)
        if announced or (due and self.chars != self._emitted_chars):
            return [self._event("writing", now)]
        return []

    def finish(self, now: float) -> list[AgentEvent]:
        """The block closed. Announce that the input is complete."""
        return [self._event("written", now)]

    def _event(self, state: str, now: float) -> AgentEvent:
        self._last_emit = now
        self._emitted_chars = self.chars
        payload = {"id": self.tool_use_id, "tool": self.tool,
                   "state": state, "chars": self.chars}
        return AgentEvent(kind=DRAFT, path=self.path, payload=json.dumps(payload))
