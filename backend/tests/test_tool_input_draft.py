# backend/tests/test_tool_input_draft.py — agent/tool_input_draft
#
# Bedrock sends tool input in small pieces as `input_json_delta` (measured 2026-10-07: 583 pieces
# for one Korean document, around 5 characters per piece). Pieces break anywhere in the JSON
# string, so the unpacked result must be **independent of where it is cut** — the tests below also
# pin the worst case of feeding one character at a time.
from __future__ import annotations

import json

from aipds.agent.tool_input_draft import (
    EMIT_INTERVAL_S, JsonStringFields, ToolInputDraft,
)

WS = "/ws"


def _rel(fp: str) -> str | None:
    return fp[len(WS) + 1:] if fp.startswith(WS + "/") else None


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _decode(raw: str, size: int, keys=("file_path", "content")) -> dict[str, str]:
    f = JsonStringFields(set(keys))
    for chunk in _chunks(raw, size):
        f.feed(chunk)
    return f.values


def _payloads(events):
    return [json.loads(e.payload) for e in events]


# ---- JsonStringFields: the same value regardless of where it is cut ----

def test_decoding_does_not_depend_on_where_the_stream_is_cut():
    body = '# 상권 분석\n\n"따옴표"와 \\역슬래시\\, 탭\t, 이모지 🍗 와 한글.'
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/x.md", "content": body})
    for size in (1, 2, 3, 5, 7, 64, len(raw)):
        values = _decode(raw, size)
        assert values["content"] == body, size
        assert values["file_path"] == "/ws/aiplc-docs/discovery/x.md", size


def test_unicode_escapes_split_across_chunks_including_surrogate_pairs():
    """Input serialized with `ensure_ascii` carries Korean as `\\uXXXX` and emoji as surrogate
    pairs. Even if cut in the middle of the four digits or between a pair, it must come back
    as one character."""
    body = "치킨 🍗 상권"
    raw = json.dumps({"content": body}, ensure_ascii=True)
    for size in (1, 2, 3, 4, 5, 6):
        assert _decode(raw, size)["content"] == body, size


def test_only_requested_top_level_strings_are_kept():
    raw = json.dumps({"file_path": "/ws/a.md",
                      "edits": [{"old_string": "x", "new_string": "content"}],
                      "replace_all": False, "content": "본문"})
    values = _decode(raw, 3)
    assert values == {"file_path": "/ws/a.md", "content": "본문"}


def test_a_key_named_like_a_value_is_not_confused_with_one():
    """A `"content":` inside a value string is not a key."""
    raw = json.dumps({"file_path": '"content": "가짜"', "content": "진짜"})
    values = _decode(raw, 1)
    assert values["content"] == "진짜"
    assert values["file_path"] == '"content": "가짜"'


def test_completion_is_reported_only_after_the_closing_quote():
    f = JsonStringFields({"file_path"})
    f.feed('{"file_path": "/ws/aiplc-docs/a')
    assert "file_path" not in f.complete
    f.feed('.md", "content": "')
    assert "file_path" in f.complete
    assert f.values["file_path"] == "/ws/aiplc-docs/a.md"


# ---- ToolInputDraft: carries only the path and character count ----

def _write(draft: ToolInputDraft, raw: str, size: int, step: float = 0.05):
    events, now = [], 0.0
    events += draft.start(now)
    for chunk in _chunks(raw, size):
        now += step
        events += draft.feed(chunk, now)
    events += draft.finish(now)
    return events


def test_a_write_reports_its_path_and_growing_length_but_never_the_body():
    body = "# Discovery Document\n\n" + "가" * 400
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/discovery-document.md",
                      "content": body})
    events = _write(ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel), raw, size=5)
    payloads = _payloads(events)

    assert payloads[0]["state"] == "writing" and events[0].path is None
    assert payloads[-1] == {"id": "t1", "tool": "Write", "state": "written", "chars": len(body)}
    assert events[-1].path == "aiplc-docs/discovery/discovery-document.md"
    counts = [p["chars"] for p in payloads]
    assert counts == sorted(counts) and len(set(counts)) > 3     # goes up
    assert not any("가" in e.payload for e in events)              # the body is not carried


def test_events_are_throttled_not_one_per_chunk():
    """Emitting per piece would be hundreds per document."""
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/x.md", "content": "나" * 2000})
    draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
    events = _write(draft, raw, size=5, step=EMIT_INTERVAL_S / 10)
    chunks = len(_chunks(raw, 5))
    assert len(events) < chunks / 5, (len(events), chunks)


def test_the_path_is_announced_as_soon_as_it_is_complete():
    draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
    draft.start(0.0)
    assert draft.feed('{"file_path": "/ws/aiplc-docs/discovery/', 0.01) == []
    events = draft.feed('x.md", "content": "', 0.02)   # before the interval, but the path is settled
    assert [e.path for e in events] == ["aiplc-docs/discovery/x.md"]


def test_an_edit_counts_its_new_string():
    raw = json.dumps({"file_path": "/ws/aiplc-docs/audit.md",
                      "old_string": "# Audit", "new_string": "# Audit\n" + "다" * 50})
    payloads = _payloads(_write(ToolInputDraft("t1", "Edit", _rel), raw, size=4))
    assert payloads[-1]["chars"] == len("# Audit\n") + 50


def test_body_before_path_still_counts():
    """Order is not guaranteed. Even if the body comes first, the path and character count
    must be right."""
    body = "먼저 온 본문"
    raw = json.dumps({"content": body, "file_path": "/ws/aiplc-docs/discovery/x.md"})
    events = _write(ToolInputDraft("t1", "Write", _rel), raw, size=3)
    assert events[-1].path == "aiplc-docs/discovery/x.md"
    assert json.loads(events[-1].payload)["chars"] == len(body)


def test_a_path_outside_the_workspace_has_no_path():
    raw = json.dumps({"file_path": "/etc/passwd.md", "content": "x" * 50})
    events = _write(ToolInputDraft("t1", "Write", _rel), raw, size=4)
    assert all(e.path is None for e in events)
