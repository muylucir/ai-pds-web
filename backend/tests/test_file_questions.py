# backend/tests/test_file_questions.py
#
# The moment a question file is written, read that file **deterministically** and ask.
#
# **Why this path exists (2026-08-17 test-wf measurement).** Currently the agent writes
# a question file and then **rebuilds** the same content as AskUserQuestion input. In
# that regeneration, 15 of 19 questions (79%) were corrupted:
#
#     verbatim                            4 (21%)
#     character substitution (garbled)  11 (58%)   ← absent from the file; occurs only in the tool call
#     abbreviated, so matching failed     4 (21%)   ← the answer was never recorded in the file
#
# This is what actually appeared on participants' screens: `푸로토하이프가 가장 먼저 증명해야 하는 것은
# 어느 쉘입니까?` (meant 프로토타입/어느 쪽), `이상적인 솜루션은 어떤 모심입니까?`.
# This is claude-code#83033 (the model mistypes `\uXXXX` escapes); reading the file as-is
# **eliminates that entire class of failure.**
#
# It is also a return to the upstream contract: AI-PLC assumes "the agent writes a
# question file, a human fills in `[Answer]:`, and the agent reads it back." This path
# implements that contract as a GUI without overriding it.
#
# **Why a PostToolUse hook.** There is no new tool for the model to learn — the trigger is
# the Write that the upstream rules already instruct. And the hook does not wait for a
# human: it immediately stops just the turn with `continue_: False`, the answer goes into
# the file, and the next turn reads that file. The measured termination signal is
# `terminal_reason='hook_stopped'` + `is_error=False`, so the existing `_translate`
# already handles it as a normal `done`.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipds.agent.claude_driver import ClaudeDriver
from tests.fakes.in_memory_s3 import FakeS3Store

QUESTION_MD = """# 페인 포인트 — 명확화 질문

서두.

## 모호성 1: 두 답변이 다른 문제를 겨냥함

Question 2에서는 C를 고르셨습니다.

그런데 Question 5에서는 D를 고르셨습니다.

### Clarification Question 1
어느 쪽입니까?

A) 왼쪽
B) 오른쪽
X) 기타 (아래 [Answer]: 태그 뒤에 설명해 주세요)

[Answer]:
"""


def _driver(tmp_path: Path) -> tuple[ClaudeDriver, Path]:
    ws = tmp_path / "ws"
    (ws / "aiplc-docs" / "discovery" / "envision").mkdir(parents=True)
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: None)
    return d, ws


def _write(ws: Path, rel: str, text: str) -> dict:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"tool_name": "Write", "tool_input": {"file_path": str(p)}}


REL = "aiplc-docs/discovery/envision/pain-point-clarification-questions.md"


async def _post(d: ClaudeDriver, payload: dict) -> dict:
    return await d._on_post_tool_use(payload, "t1", None)


def _questions_events(d: ClaudeDriver) -> list:
    return [e for e in d._queue if e.kind == "questions"]


# ---- Triggering ----

@pytest.mark.asyncio
async def test_writing_a_question_file_asks_and_stops_the_turn(tmp_path):
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD))
    # Stop the turn — otherwise the model rebuilds the same question via AskUserQuestion
    # (the 79%-corruption path) and it appears on screen twice.
    assert out.get("continue_") is False, out
    assert out.get("stopReason")
    evs = _questions_events(d)
    assert len(evs) == 1


@pytest.mark.asyncio
async def test_the_payload_carries_the_file_and_the_parsed_questions(tmp_path):
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    payload = json.loads(_questions_events(d)[0].payload)
    # `file` is the discriminator: there is no parked turn, so the frontend PUTs the answer to this file.
    assert payload["file"] == REL
    assert payload["interrupt_id"] == ""
    qs = payload["questions"]["questions"]
    assert len(qs) == 1
    assert qs[0]["number"] == 1
    # Verbatim — this is the reason this path exists.
    assert qs[0]["ask"] == "어느 쪽입니까?"
    assert [o["letter"] for o in qs[0]["options"]] == ["A", "B", "X"]
    # And it carries "why this is being asked" too (the context from ef24791).
    assert "Question 2에서는 C를 고르셨습니다." in qs[0]["context"]


@pytest.mark.asyncio
async def test_file_changed_is_still_emitted(tmp_path):
    """A question file is also an artifact — the document panel refreshes on this event."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    assert any(e.kind == "file_changed" and e.path == REL for e in d._queue)


# ---- Cases that must not trigger ----

@pytest.mark.asyncio
async def test_an_ordinary_document_does_not_ask(tmp_path):
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, "aiplc-docs/discovery/pain-point-analysis.md",
                                "# 분석\n\n본문이다.\n"))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_a_fully_answered_file_does_not_ask(tmp_path):
    """Do not ask again on read-back or rewrite."""
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD.replace("[Answer]:", "[Answer]: A")))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_an_unparseable_file_tells_the_agent_instead_of_going_silent(tmp_path):
    """Do **not silently skip** a parse failure.

    At first we made it pass silently ("degradation, not blocking"). That call was only
    right while AskUserQuestion was alive as a fallback — now that the tool is denied, a
    parse failure means **the question is lost entirely**. That is exactly what happened
    in ws-j on 2026-08-17: the file was created, no card appeared, and nothing was
    said in chat.

    Do not stop the turn; notify via `additionalContext`. If we stopped, the model would
    have no chance to read the reason and fix it, and the user would be stuck. Measured
    (2026-08-17): the model reads this note, fixes and rewrites the file **within the
    same turn**, and that rewrite fires the hook again so a proper card appears."""
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, "# 제목\n\n[Answer]:\n"))
    assert out.get("continue_") is not False, "턴을 멈추면 안 된다"
    note = (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    assert note, out
    # It must point out what to fix — given only the reason, the model rewrites the same file.
    assert "## Question" in note
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_the_same_unparseable_content_is_not_reported_twice(tmp_path):
    """Do not repeat the same note for the same content — prevents an endless loop within a turn.

    If the content **changes**, notify again: that is the case where the model rewrote the
    file and it is still wrong, and staying silent then would lose the question again."""
    d, ws = _driver(tmp_path)
    broken = "# 제목\n\n[Answer]:\n"
    first = await _post(d, _write(ws, REL, broken))
    assert (first.get("hookSpecificOutput") or {}).get("additionalContext")
    second = await _post(d, _write(ws, REL, broken))
    assert second == {}, second
    # Different (still wrong) content gets notified again.
    third = await _post(d, _write(ws, REL, broken + "\n다른 내용\n"))
    assert (third.get("hookSpecificOutput") or {}).get("additionalContext")


@pytest.mark.asyncio
async def test_the_same_unanswered_set_is_not_asked_twice(tmp_path):
    """Does not re-trigger when the agent rewrites or Edits the same file."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    d._queue.clear()
    out = await _post(d, _write(ws, REL, QUESTION_MD + "\n<!-- 주석 추가 -->\n"))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_a_newly_added_question_asks_again(tmp_path):
    """If questions are added after answering, those must be asked.

    The re-trigger guard keys on the **set of unanswered questions**, not on the file."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    d._queue.clear()
    answered_plus_new = QUESTION_MD.replace("[Answer]:", "[Answer]: A") + """
### Clarification Question 2
언제입니까?

A) 지금
B) 나중

[Answer]:
"""
    out = await _post(d, _write(ws, REL, answered_plus_new))
    assert out.get("continue_") is False
    qs = json.loads(_questions_events(d)[0].payload)["questions"]["questions"]
    # Send the whole file — already-answered questions show as filled in, so context is kept.
    assert [(q["number"], q["answer"]) for q in qs] == [(1, "A"), (2, None)]


@pytest.mark.asyncio
async def test_enabled_by_default(tmp_path, monkeypatch):
    """**It is on by default.** We flipped it after confirming one full loop with a real
    Discovery turn (2026-08-17): the hook read the question file and showed the card, the
    turn stopped, the answer was recorded in the file, and on the next turn the model read
    that answer and continued the workflow — that last point was the only unverified item."""
    monkeypatch.delenv("AIPDS_FILE_QUESTIONS", raising=False)
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD))
    assert out.get("continue_") is False
    assert len(_questions_events(d)) == 1


@pytest.mark.asyncio
async def test_can_be_switched_off(tmp_path, monkeypatch):
    """An escape hatch remains — setting the env to falsy returns to the old AskUserQuestion path.

    On the instance, user-data injects the value via systemd `Environment=`, so editing
    that file would require replacing the instance. Instead, creating a gitignored
    `backend/.env` lets you turn it off without redeploying, because `aipds-update` does
    not revert it (it is an untracked file)."""
    monkeypatch.setenv("AIPDS_FILE_QUESTIONS", "false")
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD))
    assert out == {}
    assert not _questions_events(d)
    # The artifact event is still emitted.
    assert any(e.kind == "file_changed" for e in d._queue)


# ---- Restore after refresh/restart (GET /pending) ----
# The `questions` event the hook emits is **live, once** only. If the user refreshes the
# browser after receiving the card, that event does not come again, so `GET /pending`
# must bring the card back. On the old path, `pending_store` did that job.
#
# **The storage mechanism was never the problem — fuzzy matching was.** So we use that
# mechanism but shrink what it holds to **a single file path**. Question content is not
# stored; it is re-read from the file every time:
#
#   - Always current. If the agent edits the file, the card follows.
#   - **No clear stage is needed.** `runner.write_file` writes directly to S3
#     (runner.py:57-59), so S3 is current the moment the answer is recorded, and when
#     there are no unanswered questions pending() naturally becomes None. There is
#     structurally no path where a forgotten delete leaves a dead card behind.
#
# The path is stored because of ambiguity: a measured project had **3 unanswered files at
# once** (the result of lost answers). A scan alone cannot tell which round is open, and
# showing the wrong card is worse than showing none.

@pytest.mark.asyncio
async def test_the_hook_records_which_file_is_open(tmp_path):
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    from aipds.agent.pending_store import load_pending_file
    assert await load_pending_file(d._s3) == REL


@pytest.mark.asyncio
async def test_pending_rebuilds_the_card_from_s3_after_a_refresh(tmp_path):
    """Must restore even with no in-memory state (= restart)."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    # When a turn ends the runner uploads the workspace to S3 — set up that state.
    await d._s3.put(REL, QUESTION_MD)
    # Pretend to be a new process: clear the in-memory pending state.
    d._pending_payload = None
    payload = json.loads(await d.pending({"session_id": "s-1"}))
    assert payload["file"] == REL
    assert payload["interrupt_id"] == ""
    qs = payload["questions"]["questions"]
    assert [q["number"] for q in qs] == [1]
    assert qs[0]["ask"] == "어느 쪽입니까?"


@pytest.mark.asyncio
async def test_pending_goes_away_once_the_answers_are_in_the_file(tmp_path):
    """No clear is called — it is derived from the file, so the answer itself is the end signal."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    await d._s3.put(REL, QUESTION_MD.replace("[Answer]:", "[Answer]: A"))
    d._pending_payload = None
    assert await d.pending({"session_id": "s-1"}) is None


@pytest.mark.asyncio
async def test_pending_survives_a_missing_file(tmp_path):
    """If the marker exists but the file does not (deleted or path changed), quietly return None.

    Restore is a convenience, so it does not raise a 500 — the same discipline as pending_store."""
    d, _ = _driver(tmp_path)
    from aipds.agent.pending_store import save_pending_file
    await save_pending_file(d._s3, file="aiplc-docs/gone.md")
    d._pending_payload = None
    assert await d.pending({"session_id": "s-1"}) is None


# ---- AskUserQuestion is no longer used ----
# Not removal but **denial + a replacement instruction**. Simply removing the interception
# would make the question vanish silently the moment the model calls the tool — no card on
# screen and nothing in chat. Denial hands "write a question file" back to the model, so
# that hole never opens. `write_outside_docs` uses the same pattern (deny alone and the
# model just changes the path and retries, falling into a loop).
#
# There is one switch: when `AIPDS_FILE_QUESTIONS` is on, the file path is the only path;
# when off, the old interception runs as before. If both paths were alive at once, the same
# question would appear on screen twice.

def _ctx():
    class _C:
        tool_use_id = "toolu_x"
    return _C()


@pytest.mark.asyncio
async def test_ask_user_question_is_denied_when_file_questions_are_on(tmp_path):
    d, _ = _driver(tmp_path)
    result = await d._on_can_use_tool(
        "AskUserQuestion",
        {"questions": [{"question": "누구?", "header": "대상",
                        "options": [{"label": "PM"}, {"label": "PO"}]}]},
        _ctx())
    assert type(result).__name__ == "PermissionResultDeny", result
    # It must state the replacement action — deny alone and the model retries the same tool.
    assert "[Answer]:" in result.message
    # And the question was not lost: no card appears and nothing is parked.
    assert not _questions_events(d)
    assert d._pending_question is None


@pytest.mark.asyncio
async def test_ask_user_question_still_works_when_file_questions_are_off(
        tmp_path, monkeypatch):
    """With the switch off the old path is unchanged — it must be reversible."""
    monkeypatch.setenv("AIPDS_FILE_QUESTIONS", "false")
    d, _ = _driver(tmp_path)
    # **Do not await directly.** On the off path, parking on a future while waiting for
    # the answer is the normal behavior, so awaiting would never return (the real SDK
    # also calls it from a separate task — tests/fakes/fake_sdk_asking.AskingSdkClient
    # has that shape).
    import asyncio
    task = asyncio.create_task(d._on_can_use_tool(
        "AskUserQuestion",
        {"questions": [{"question": "누구?", "header": "대상",
                        "options": [{"label": "PM"}, {"label": "PO"}]}]},
        _ctx()))
    for _ in range(4):
        await asyncio.sleep(0)
    assert not task.done(), "파킹되지 않았다 — 옛 경로가 깨졌다"
    assert d._pending_question is not None
    assert len(_questions_events(d)) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_other_tools_are_unaffected(tmp_path):
    d, _ = _driver(tmp_path)
    result = await d._on_can_use_tool("Write", {"file_path": "x.md"}, _ctx())
    assert type(result).__name__ == "PermissionResultAllow"


@pytest.mark.asyncio
async def test_the_question_file_is_in_s3_before_the_card_is_advertised(tmp_path):
    """The hook uploads the file to S3 **directly** — it does not wait for the end-of-turn sync.

    Failure measured 2026-08-17: in a real turn the hook fired and the card appeared, but
    `GET /pending` returned `file=None` and answer submission returned 404. The hook reads
    the local file and writes only the marker to S3, while the question file itself is
    uploaded by the runner **after the turn ends** (the runner's done/error sync). The
    gap between them is the window:

      - `pending()` cannot find the file the marker points to in S3 and falls back to None
        (refresh and the card disappears)
      - answer submission returns 404 because `runner.read_file` reads S3

    The moment the card is advertised, that file must already be in the source of truth
    (S3). We already hold the content in hand, so uploading here is the cheapest and most
    reliable option — one S3 put per round.
    """
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    assert await d._s3.get(REL) == QUESTION_MD
    # And it must exist before the marker — if the marker ever points to a missing file,
    # both failures above reproduce exactly.
    from aipds.agent.pending_store import load_pending_file
    assert await load_pending_file(d._s3) == REL


# ---- audit.md is never a question file, whatever it contains (measured 2026-08-18) ----
# `core-workflow.md:303-304` requires of audit.md: "**MANDATORY**: Log ALL user
# inputs ... Capture user's COMPLETE RAW INPUT exactly as provided".
# Faithfully recording an answer round puts `[Answer]: A` at the start of a line.
#
# This hook then caught audit.md as a question file, and the agent reacted like this
# (translated): "The [Answer] tag text written in the audit record was caught by the
# question parser — since audit.md is not a question file, I will remove that notation
# and keep only the record." In other words, our detection **damaged the audit record
# that the upstream required to be kept verbatim.**

@pytest.mark.asyncio
async def test_audit_md_with_a_raw_answer_tag_does_not_ask(tmp_path):
    d, ws = _driver(tmp_path)
    audit = (
        "# AI-PLC Audit Log\n"
        "\n"
        "## 2026-08-18 — Envision Step 0.1 답변 수신 (원문)\n"
        "\n"
        "`business-context-questions.md` 1번 문항의 답변 태그 원문:\n"
        "\n"
        "## Question 1\n"
        "\n"
        "A) 자유 서술\n"
        "B) 구조화된 질문\n"
        "\n"
        # An empty slot — copying the question file the agent just made verbatim produces
        # this shape. This is the state that actually got caught by the parser.
        "[Answer]:\n"
    )
    out = await _post(d, _write(ws, "aiplc-docs/audit.md", audit))
    assert out == {}, "감사 기록으로 턴을 멈추면 에이전트가 그 기록을 지운다"
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_aiplc_state_md_with_a_raw_answer_tag_does_not_ask(tmp_path):
    """The state file is the same — it is our file and may quote answers."""
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, "aiplc-docs/aiplc-state.md",
                                "# AI-PLC State\n\n## Question 1\n\nA) x\nB) y\n\n[Answer]:\n"))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_a_quoted_answer_tag_does_not_ask(tmp_path):
    """A quotation not at the start of a line is not a question in any file.

    While the hook used a simple `"[Answer]:" in md` containment check, this was caught
    too — its verdict diverged from the write-back side (`^` anchor).
    """
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, "aiplc-docs/discovery/notes.md",
                                "# 메모\n\n답변 태그는 `[Answer]: B` 꼴로 적는다.\n"))
    assert out == {}
    assert not _questions_events(d)
