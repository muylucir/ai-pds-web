# backend/tests/test_tool_input_draft.py — agent/tool_input_draft
#
# Bedrock은 도구 입력을 `input_json_delta`로 잘게 보낸다(2026-10-07 실측: 한국어 문서
# 하나에 583조각, 한 조각 5자 안팎). 조각은 JSON 문자열의 아무 곳에서나 끊기므로,
# 풀어낸 결과가 **자르는 위치와 무관해야** 한다 — 아래 테스트들은 한 글자씩 넣는
# 최악의 경우를 함께 고정한다.
from __future__ import annotations

import json

from aipds.agent.tool_input_draft import (
    EMIT_INTERVAL_S, JsonStringFields, ToolInputDraft, discarded,
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


# ---- ToolInputDraft: 무엇을 화면에 싣는가 ----

def _write(draft: ToolInputDraft, raw: str, size: int, step: float = 0.05):
    events, now = [], 0.0
    events += draft.start(now)
    for chunk in _chunks(raw, size):
        now += step
        events += draft.feed(chunk, now)
    events += draft.finish(now)
    return events


def test_a_document_write_streams_its_body_and_ends_written():
    body = "# Discovery Document\n\n" + "가" * 400
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/discovery-document.md",
                      "content": body})
    draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
    events = _write(draft, raw, size=5)
    payloads = _payloads(events)

    assert payloads[0]["state"] == "writing" and events[0].path is None
    assert "".join(p.get("append", "") for p in payloads) == body
    assert payloads[-1]["state"] == "written"
    assert payloads[-1]["chars"] == len(body)
    assert events[-1].path == "aiplc-docs/discovery/discovery-document.md"
    assert all(p["id"] == "t1" for p in payloads)


def test_events_are_throttled_not_one_per_chunk():
    """조각마다 흘리면 문서 하나에 수백 개다."""
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/x.md", "content": "나" * 2000})
    draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
    step = EMIT_INTERVAL_S / 10
    events = _write(draft, raw, size=5, step=step)
    chunks = len(_chunks(raw, 5))
    assert len(events) < chunks / 5, (len(events), chunks)


def test_the_path_is_announced_as_soon_as_it_is_complete():
    draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
    draft.start(0.0)
    assert draft.feed('{"file_path": "/ws/aiplc-docs/discovery/', 0.01) == []
    events = draft.feed('x.md", "content": "', 0.02)   # 간격 전이지만 경로가 확정됐다
    assert [e.path for e in events] == ["aiplc-docs/discovery/x.md"]


def test_record_keeping_and_question_files_carry_counts_but_never_text():
    """audit.md는 정본에 올릴 때 자격증명을 지우는 파일이다 — 조각 단위 리댁션은 조각
    경계에 걸친 값을 놓치므로 본문을 흘리지 않는다. 질문 파일은 폼으로 그려질 원문이다."""
    for path in ("aiplc-docs/audit.md", "aiplc-docs/aiplc-state.md",
                 "aiplc-docs/discovery/envision/pain-point-questions.md"):
        raw = json.dumps({"file_path": f"/ws/{path}", "content": "AKIA" + "x" * 300})
        draft = ToolInputDraft(tool_use_id="t1", tool="Write", to_rel=_rel)
        payloads = _payloads(_write(draft, raw, size=4))
        assert not any("append" in p for p in payloads), path
        assert payloads[-1]["chars"] == 304, path


def test_an_edit_reports_progress_without_a_preview():
    """`new_string`은 문서의 조각이다 — 미리보기로 띄우면 문서가 사라진 것처럼 보인다."""
    raw = json.dumps({"file_path": "/ws/aiplc-docs/discovery/discovery-document.md",
                      "old_string": "# Part 1", "new_string": "# Part 1\n" + "다" * 50})
    draft = ToolInputDraft(tool_use_id="t1", tool="Edit", to_rel=_rel)
    payloads = _payloads(_write(draft, raw, size=4))
    assert not any("append" in p for p in payloads)
    assert payloads[-1]["chars"] == len("# Part 1\n") + 50


def test_body_before_path_is_held_until_the_path_decides():
    """순서는 보장되지 않는다. 본문이 먼저 와도 문서면 빠짐없이, 아니면 하나도 싣지 않는다."""
    body = "먼저 온 본문"
    doc = json.dumps({"content": body, "file_path": "/ws/aiplc-docs/discovery/x.md"})
    payloads = _payloads(_write(ToolInputDraft("t1", "Write", _rel), doc, size=3))
    assert "".join(p.get("append", "") for p in payloads) == body

    audit = json.dumps({"content": body, "file_path": "/ws/aiplc-docs/audit.md"})
    payloads = _payloads(_write(ToolInputDraft("t2", "Write", _rel), audit, size=3))
    assert not any("append" in p for p in payloads)


def test_a_path_outside_the_workspace_never_streams():
    raw = json.dumps({"file_path": "/etc/passwd.md", "content": "x" * 50})
    events = _write(ToolInputDraft("t1", "Write", _rel), raw, size=4)
    assert all(e.path is None for e in events)
    assert not any("append" in p for p in _payloads(events))


def test_discarded_names_the_draft_to_drop():
    ev = discarded("t9", "Write", "aiplc-docs/discovery/prototype/survey-aggregate.md")
    assert ev.kind == "draft"
    assert json.loads(ev.payload) == {"id": "t9", "tool": "Write", "state": "discarded"}
