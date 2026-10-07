# backend/tests/test_tool_input_draft.py — agent/tool_input_draft
#
# Bedrock은 도구 입력을 `input_json_delta`로 잘게 보낸다(2026-10-07 실측: 한국어 문서
# 하나에 583조각, 한 조각 5자 안팎). 조각은 JSON 문자열의 아무 곳에서나 끊기므로,
# 풀어낸 결과가 **자르는 위치와 무관해야** 한다 — 아래 테스트들은 한 글자씩 넣는
# 최악의 경우를 함께 고정한다.
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


# ---- JsonStringFields: 자르는 위치와 무관하게 같은 값 ----

def test_decoding_does_not_depend_on_where_the_stream_is_cut():
    body = '# 상권 분석\n\n"따옴표"와 \\역슬래시\\, 탭\t, 이모지 🍗 와 한글.'
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/x.md", "content": body})
    for size in (1, 2, 3, 5, 7, 64, len(raw)):
        values = _decode(raw, size)
        assert values["content"] == body, size
        assert values["file_path"] == "/ws/aiplc-docs/discovery/x.md", size


def test_unicode_escapes_split_across_chunks_including_surrogate_pairs():
    """`ensure_ascii`로 직렬화된 입력은 한글을 `\\uXXXX`로, 이모지를 서로게이트 쌍으로
    싣는다. 네 자리 중간이나 쌍 사이에서 끊겨도 한 글자로 돌아와야 한다."""
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
    """값 문자열 안의 `"content":` 는 키가 아니다."""
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


# ---- ToolInputDraft: 경로와 글자 수만 싣는다 ----

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
    assert counts == sorted(counts) and len(set(counts)) > 3     # 올라간다
    assert not any("가" in e.payload for e in events)              # 본문은 싣지 않는다


def test_events_are_throttled_not_one_per_chunk():
    """조각마다 흘리면 문서 하나에 수백 개다."""
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/x.md", "content": "나" * 2000})
    draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
    events = _write(draft, raw, size=5, step=EMIT_INTERVAL_S / 10)
    chunks = len(_chunks(raw, 5))
    assert len(events) < chunks / 5, (len(events), chunks)


def test_the_path_is_announced_as_soon_as_it_is_complete():
    draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
    draft.start(0.0)
    assert draft.feed('{"file_path": "/ws/aiplc-docs/discovery/', 0.01) == []
    events = draft.feed('x.md", "content": "', 0.02)   # 간격 전이지만 경로가 확정됐다
    assert [e.path for e in events] == ["aiplc-docs/discovery/x.md"]


def test_an_edit_counts_its_new_string():
    raw = json.dumps({"file_path": "/ws/aiplc-docs/audit.md",
                      "old_string": "# Audit", "new_string": "# Audit\n" + "다" * 50})
    payloads = _payloads(_write(ToolInputDraft("t1", "Edit", _rel), raw, size=4))
    assert payloads[-1]["chars"] == len("# Audit\n") + 50


def test_body_before_path_still_counts():
    """순서는 보장되지 않는다. 본문이 먼저 와도 경로와 글자 수가 맞아야 한다."""
    body = "먼저 온 본문"
    raw = json.dumps({"content": body, "file_path": "/ws/aiplc-docs/discovery/x.md"})
    events = _write(ToolInputDraft("t1", "Write", _rel), raw, size=3)
    assert events[-1].path == "aiplc-docs/discovery/x.md"
    assert json.loads(events[-1].payload)["chars"] == len(body)


def test_a_path_outside_the_workspace_has_no_path():
    raw = json.dumps({"file_path": "/etc/passwd.md", "content": "x" * 50})
    events = _write(ToolInputDraft("t1", "Write", _rel), raw, size=4)
    assert all(e.path is None for e in events)
