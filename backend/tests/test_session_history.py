# History restore for the claude driver (CLI transcript).
#
# Previously the first half of this file checked the Bedrock Converse shape of the strands
# fallback driver (toolUse/toolResult, camelCase). When that driver was deleted, those tests
# were removed along with transform_messages — what remains is only the CLI's Anthropic Messages
# shape (block["type"] == "tool_use").
import json

import pytest

from fakes.in_memory_s3 import FakeS3Store
from aipds.agent.session_store import DiscoverySessionStore
from aipds.models import HistoryItem
from aipds.session_history import list_history, transform_cli_transcript


def _cli(type_, role=None, content=None, **extra):
    """One CLI transcript line. Can also build bookkeeping lines without a message."""
    d = {"type": type_, **extra}
    if role is not None:
        d["message"] = {"role": role, "content": content}
    return d


def test_cli_bookkeeping_lines_are_skipped():
    # Measured: the real transcript has lines without a message mixed in, such as
    # queue-operation/attachment/ai-title/last-prompt. They are not conversation, so they
    # are not restored.
    raw = [
        _cli("queue-operation", operation="enqueue"),
        _cli("attachment"),
        _cli("ai-title"),
        _cli("user", "user", "시작해줘"),
        _cli("last-prompt"),
    ]
    items = transform_cli_transcript(raw)
    assert [(i.role, i.text) for i in items] == [("user", "시작해줘")]


def test_cli_injected_user_lines_are_not_user_bubbles():
    # The skill body (isMeta) and compaction summary (isCompactSummary) come with the user
    # role but are not something a person said. As bubbles, they split a turn in two where
    # there is no real turn boundary.
    raw = [
        _cli("user", "user", "시작해줘"),
        _cli("assistant", "assistant", [{"type": "text", "text": "스킬을 씁니다"}]),
        _cli("user", "user", [{"type": "text", "text": "Base directory for this skill: …"}],
             isMeta=True),
        _cli("user", "user", "This session is being continued from a previous conversation…",
             isCompactSummary=True),
        _cli("assistant", "assistant", [{"type": "text", "text": "계속합니다"}]),
    ]
    items = transform_cli_transcript(raw)
    assert [(i.role, i.text) for i in items] == [
        ("user", "시작해줘"), ("ai", "스킬을 씁니다\n계속합니다")]


def test_cli_plain_string_content_is_not_iterated_per_character():
    # Measured: the content of the first user line is a plain string, not a list. Without
    # normalization the block loop walks the string character by character and nothing is left.
    items = transform_cli_transcript([_cli("user", "user", "Say OK")])
    assert [(i.role, i.text) for i in items] == [("user", "Say OK")]


def test_cli_text_and_tools_match_the_live_representation():
    # It must be the same representation as live (claude_driver._translate) so the
    # scrollback does not look different from the screen just seen: text as bubbles, file
    # tools as file_changed, other tools as status.
    raw = [_cli("assistant", "assistant", [
        {"type": "text", "text": "문서를 씁니다."},
        {"type": "tool_use", "id": "t1", "name": "Write",
         "input": {"file_path": "aiplc-docs/audit.md"}},
        {"type": "tool_use", "id": "t2", "name": "Read",
         "input": {"file_path": "rules.md"}},
    ])]
    ai = next(i for i in transform_cli_transcript(raw) if i.role == "ai")
    assert ai.text == "문서를 씁니다."
    assert [t.model_dump() for t in ai.trace] == [
        # File tools carry the path as `path` — the frontend draws "📝 파일 변경: …".
        {"kind": "file_changed", "text": None, "path": "aiplc-docs/audit.md",
         "detail": None},
        # Other tools carry the name + **what it did**. Live sends the same value
        # (both use tool_trace.tool_detail) — that is the point of this test.
        {"kind": "status", "text": "Read", "path": None, "detail": "rules.md"},
    ]


def test_cli_bash_commands_are_redacted_in_the_restored_trace():
    """Bash commands are where credentials show up.

    Live, `routes/turns._redacted` scans the payload. The restore must pass through this
    function directly — if only one side does it, a refresh becomes a path that bypasses
    redaction."""
    raw = [_cli("assistant", "assistant", [
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE ls"}},
    ])]
    ai = next(i for i in transform_cli_transcript(raw) if i.role == "ai")
    detail = ai.trace[0].detail or ""
    assert "AKIAIOSFODNN7EXAMPLE" not in detail, detail


def test_cli_one_turn_is_one_bubble_not_one_per_tool_call():
    """One turn is one bubble — an empty bubble must not be created per tool call.

    Measured transcript structure (real CLI, a turn with 5 tool uses):

        assistant [thinking]
        assistant [tool_use]   user [tool_result]   × 5
        assistant [text]       <- only here does human-readable content appear

    The CLI writes a separate assistant line per tool call. Creating an item per line splits a
    turn that was one bubble live into 6, 5 of which are empty bubbles with no text
    (measured: 1 live message event -> 6 restored ai items, 5 empty). On screen, empty gray
    boxes with only "추론 과정" (reasoning) attached appear one after another.

    Live creates one AiItem per turn, accumulates message into that one, and stacks tools
    onto the same item's trace (useTurnStream.ts:108,121-123). The restore must do the same.
    """
    raw = [
        _cli("user", "user", "두 파일을 쓰고 확인해줘"),
        _cli("assistant", "assistant", [{"type": "thinking", "thinking": "..."}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t1", "name": "Write",
             "input": {"file_path": "a.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t2", "name": "Write",
             "input": {"file_path": "b.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t3", "name": "Read",
             "input": {"file_path": "a.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t3", "content": "..."}]),
        _cli("assistant", "assistant", [{"type": "text", "text": "완료"}]),
    ]
    items = transform_cli_transcript(raw)

    assert [i.role for i in items] == ["user", "ai"], \
        [(i.role, i.text) for i in items]
    ai = items[1]
    assert ai.text == "완료"
    # All 3 tools gather into that one trace.
    assert [(t.kind, t.path or t.text) for t in ai.trace] == [
        ("file_changed", "a.md"), ("file_changed", "b.md"), ("status", "Read")]


def test_cli_a_turn_that_never_produced_text_is_not_an_empty_bubble():
    """A turn that ended without text leaves only a trace, not an empty bubble.

    An interrupted turn (idle timeout, SSE drop) has this shape. Creating a bubble would leave
    an empty gray box, but what was in that spot live was a progress indicator, which is not
    something to restore.
    """
    raw = [
        _cli("user", "user", "시작해줘"),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "x.md"}}]),
    ]
    items = transform_cli_transcript(raw)
    ai = [i for i in items if i.role == "ai"]
    assert len(ai) == 1, [(i.role, i.text) for i in items]
    assert ai[0].text == ""
    assert [t.text for t in ai[0].trace] == ["Read"]


def test_cli_separate_user_turns_stay_separate():
    """Merging happens only within a turn — an actual user utterance is the boundary.

    A user line holding a tool_result is not a user utterance, so it is not a boundary (live
    renders nothing for that line). Only real input should start a new turn, so that two
    questions do not clump into one.
    """
    raw = [
        _cli("user", "user", "첫 질문"),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "a.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        _cli("assistant", "assistant", [{"type": "text", "text": "첫 답"}]),
        _cli("user", "user", "둘째 질문"),
        _cli("assistant", "assistant", [{"type": "text", "text": "둘째 답"}]),
    ]
    items = transform_cli_transcript(raw)
    assert [(i.role, i.text) for i in items] == [
        ("user", "첫 질문"), ("ai", "첫 답"),
        ("user", "둘째 질문"), ("ai", "둘째 답")]
    assert [t.text for t in items[1].trace] == ["Read"]
    assert not items[3].trace


def test_cli_question_card_comes_after_the_bubble_that_asked():
    """A card comes after the bubble that issued it.

    Live, the model first streams an explanation of "why it is asking" and then the question
    card attaches (claude_driver's _CONTACT_ADDENDUM requires that explanation). If the card
    came before the bubble, the scrollback would show the question first without an
    explanation, followed by the explanation below it — an order never seen live.

    This ordering comes from turn accumulation — while finalizing the bubble is deferred until
    the next user utterance, the card can attach right away.
    """
    raw = [
        _cli("user", "user", "시작해줘"),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "r1", "name": "Read",
             "input": {"file_path": "rules.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "r1", "content": "ok"}]),
        _cli("assistant", "assistant", [
            {"type": "text", "text": "질문 드립니다."},
            {"type": "tool_use", "id": "qa", "name": "AskUserQuestion",
             "input": {"questions": []}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "qa",
             "content": [{"text": _CLI_ANSWER_SENTENCE}]}]),
        _cli("assistant", "assistant", [{"type": "text", "text": "작성했습니다."}]),
    ]
    items = transform_cli_transcript(raw)

    assert [i.role for i in items] == ["user", "ai", "card", "user", "ai"], \
        [(i.role, i.text) for i in items]
    assert items[1].text == "질문 드립니다."
    assert [t.text for t in items[1].trace] == ["Read"]
    assert items[2].card == "questions"
    # Only checks order — the wording is covered by the test above.
    assert items[3].text.startswith("답변 제출")


def test_cli_ask_user_question_becomes_a_card_not_a_trace_row():
    raw = [_cli("assistant", "assistant", [
        {"type": "text", "text": "질문 드립니다."},
        {"type": "tool_use", "id": "ta", "name": "AskUserQuestion",
         "input": {"questions": []}},
    ])]
    items = transform_cli_transcript(raw)
    ai = next(i for i in items if i.role == "ai")
    assert not ai.trace, "카드로 표현되므로 트레이스에 중복 노출하지 않는다"
    assert any(i.role == "card" and i.card == "questions" for i in items)


#: The CLI's **own** sentence written as the AskUserQuestion result. It is neither our
#: prefix nor JSON — so the answer cannot be unpacked from this body, and answer_records
#: provides the exact values (the agent/answer_store.py header is the basis for this
#: decision).
_CLI_ANSWER_SENTENCE = ('Your questions have been answered: "질문"="보기". '
                        "You can now continue with these answers in mind.")


def test_cli_answer_tool_result_becomes_a_readable_user_bubble():
    # Only answer results must be picked — the real transcript has Write/Read tool_results
    # mixed in, so match against the AskUserQuestion id.
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "ta", "name": "AskUserQuestion",
             "input": {"questions": []}}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "tw", "name": "Write",
             "input": {"file_path": "x.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "tw",
             "content": [{"type": "text", "text": "written"}]}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "ta",
             "content": [{"type": "text", "text": _CLI_ANSWER_SENTENCE}]}]),
    ]
    users = [i for i in transform_cli_transcript(raw) if i.role == "user"]
    assert len(users) == 1, [i.text for i in users]
    # Without a record, the CLI sentence is sent as the fallback as is — better than an
    # empty bubble. The exact question numbers and letters come from answer_records
    # (agent/answer_store.py).
    assert users[0].text.startswith("답변 제출")
    assert users[0].answers is None


#: The input shape of the SDK's AskUserQuestion. Copied from a real transcript.
_SDK_QUESTIONS = [
    {"question": "무엇을 만드나요?", "header": "도메인", "multiSelect": False,
     "options": [{"label": "자동차 부품", "description": "IATF 16949 환경"},
                 {"label": "가전", "description": "소비자 제품"}]},
    {"question": "월 클레임 건수는?", "header": "규모", "multiSelect": True,
     "options": [{"label": "10건 미만"}, {"label": "10~30건"}]},
]

#: The CLI's **own** sentence written as the AskUserQuestion result. It is neither our
#: prefix nor JSON — not re-parsing this is why answer records were introduced.
_CLI_ANSWER_SENTENCE = ('Your questions have been answered: '
                        '"무엇을 만드나요?"="자동차 부품". '
                        'You can now continue with these answers in mind.')


def test_cli_answer_uses_the_answer_record_when_one_exists():
    """With a record, the restore produces the same values as live.

    Previously the English sentence written by the CLI was emitted as is as `답변 제출: <문장>` —
    the question numbers, option letters, and option text were all lost, so the same
    conversation looked different before and after a refresh. The record holds the answer
    dict exactly as we received it and the question payload at that moment, so the frontend
    builds the same text as live with answerSummary.
    """
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "tu-1", "name": "AskUserQuestion",
             "input": {"questions": _SDK_QUESTIONS}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu-1",
             "content": _CLI_ANSWER_SENTENCE}]),
    ]
    records = {"tu-1": {"tool_use_id": "tu-1", "interrupt_id": "iid",
                        "questions": {"name": "discovery-questions",
                                      "questions": [{"number": 1}]},
                        "answers": {"1": "A", "2": "A,B"}}}

    items = transform_cli_transcript(raw, answer_records=records)

    user = next(i for i in items if i.role == "user")
    assert user.answers == {"1": "A", "2": "A,B"}
    assert user.questions == records["tu-1"]["questions"]
    # The English sentence does not leak into the screen text — the frontend rebuilds it in
    # the UI language.
    assert "have been answered" not in (user.text or "")


def test_cli_answer_falls_back_to_the_sentence_without_a_record():
    """Sessions without records (before this feature) keep the former behavior — but that
    round's question payload is restored from the transcript, so the card tells what it
    asked."""
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "tu-1", "name": "AskUserQuestion",
             "input": {"questions": _SDK_QUESTIONS}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu-1",
             "content": _CLI_ANSWER_SENTENCE}]),
    ]
    items = transform_cli_transcript(raw)

    card = next(i for i in items if i.role == "card")
    assert card.questions is not None
    assert [q["text"] for q in card.questions["questions"]] == [
        "무엇을 만드나요?", "월 클레임 건수는?"]
    # Options are assembled by the same function as live (letter + "라벨 — 설명" + adding Other).
    first = card.questions["questions"][0]["options"]
    assert first[0]["letter"] == "A" and first[0]["text"] == "자동차 부품 — IATF 16949 환경"
    assert first[-1]["is_other"] is True
    user = next(i for i in items if i.role == "user")
    assert user.answers is None and "have been answered" in (user.text or "")


def test_cli_rejected_question_round_leaves_no_ghost_card():
    """A round the CLI blocked by schema validation was never shown to the user.

    Measured: 3 cases where the model passed `questions` as a JSON string were rejected
    immediately with `is_error: true`, and the model retried with a proper array on the next
    turn. Creating cards even for the rejected rounds leaves question cards nobody saw in the
    scrollback.
    """
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "bad", "name": "AskUserQuestion",
             "input": {"questions": '[{"question": "..."}]'}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "bad", "is_error": True,
             "content": "<tool_use_error>InputValidationError: ... "
                        "`questions` type is expected as `array` but provided "
                        "as `string`</tool_use_error>"}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "good", "name": "AskUserQuestion",
             "input": {"questions": _SDK_QUESTIONS}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "good",
             "content": _CLI_ANSWER_SENTENCE}]),
    ]
    items = transform_cli_transcript(raw)

    cards = [i for i in items if i.role == "card"]
    assert len(cards) == 1, [i.questions for i in cards]
    # The result of a rejected round does not create an answer bubble either (it is not an
    # answer).
    assert len([i for i in items if i.role == "user"]) == 1


def _driver_session_key(project_id: str) -> dict:
    """The session key the driver actually uses for mirroring.

    project_id must not be used as is — that was the second layer of this bug. The CLI rejects
    a non-UUID session-id, so the driver derives a uuid5 and uses that value, and if the reader
    uses the original project_id it searches a prefix where nothing was written.
    If both sides make the same mistake, the tests pass even in a mismatched state.
    """
    from aipds.agent.claude_driver import _sdk_session_id
    session_id, _ = _sdk_session_id({"session_id": project_id})
    return {"session_id": session_id}


async def test_list_history_reads_what_the_session_store_wrote():
    """That writes and reads look at the same key — the core of this bug was a path mismatch."""
    s3 = FakeS3Store()
    store = DiscoverySessionStore(s3)
    key = _driver_session_key("sess-1")
    await store.append(key, [
        {"type": "user", "message": {"role": "user", "content": "시작해줘"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "네, 시작합니다."}]}},
    ])
    # Same as the production wiring: the claude transcript is read from project_s3
    # (the driver writes to the very store it received via s3_store_factory(pid)).
    items = await list_history(s3, "sess-1")
    assert [(i.role, i.text) for i in items] == [
        ("user", "시작해줘"), ("ai", "네, 시작합니다.")]


async def test_append_across_instances_does_not_overwrite_earlier_batches():
    """resume creates a new store instance. If the sequence started at 0, the first append
    would overwrite the previous session's early batch at the same key, and the start of the
    history would vanish."""
    s3 = FakeS3Store()
    key = _driver_session_key("sess-3")
    first = DiscoverySessionStore(s3)
    await first.append(key, [{"type": "user",
                              "message": {"role": "user", "content": "첫 턴"}}])
    second = DiscoverySessionStore(s3)   # the new instance resume creates
    await second.append(key, [{"type": "user",
                               "message": {"role": "user", "content": "두 번째 턴"}}])
    items = await list_history(s3, "sess-3")
    assert [i.text for i in items] == ["첫 턴", "두 번째 턴"]


def test_cli_a_file_round_restores_the_answers_the_user_gave():
    """**Measured (2026-08-21).** When the conversation was restored by a refresh, all the user
    bubbles of file question rounds were stuck on the same machine text — "질문에 답했습니다. 답변은
    `…-questions.md`의 `[Answer]:` 태그에 들어 있으니…".

    There is nothing to join on this path: a file round does not create an `ask_questions` tool
    call (what the agent calls is `Write`), so there is no `tool_use_id` and no `answer_store`
    record. So it falls not into the tool_result branch above but into the plain-text user
    branch below, and whatever is in the transcript becomes the bubble as is.

    The fix is **to make the transcript hold the truth**
    (`prompts.file_answers_recorded`). Then this function does not change by a single line and
    the restore becomes correct — this check pins that contract from both sides.
    """
    from aipds.agent.prompts import file_answers_recorded

    turn_text = file_answers_recorded(
        "ko", "aiplc-docs/discovery/envision/pain-point-questions.md",
        {1: "B", 12: "A,C"})
    items = transform_cli_transcript([_cli("user", "user", turn_text)])

    assert len(items) == 1 and items[0].role == "user"
    restored = items[0].text
    assert "B" in restored and "A,C" in restored, restored
    # Keep it from regressing to a state where only the machine text remains.
    assert restored.strip() != "질문에 답했습니다."


def test_cli_a_file_round_restores_free_text_verbatim():
    """A free-form answer is the most important case for the restore — letters are short, but a
    free-form answer is the only record of what the user wrote.

    **Known limitation: option letters are not resolved to labels.** The live screen draws
    them with `frontend/lib/answerSummary.ts` turning letters into option text. That
    function's body is a discrimination that reverses QuestionCard's value contract — one
    string carries four shapes (`A`, `A: 부연`, `A,C`, and free text that looks like a letter)
    — and that file itself notes "telling them apart is the whole job here". Reimplementing
    it in Python would make two copies of that discrimination, and whichever drifts would be
    the silent one. So the restore holds **the submitted value as is**: free-form answers
    are intact, and letters remain letters. Matching all the way to labels would require
    sending the structured answer to the frontend so it uses the same function, and that
    join key is a separate matter.
    """
    from aipds.agent.prompts import file_answers_recorded

    written = "예산은 3분기까지 확정되지 않았습니다. 그 전에는 B로 갑니다."
    items = transform_cli_transcript([_cli("user", "user", file_answers_recorded(
        "ko", "aiplc-docs/q.md", {3: written}))])

    assert written in items[0].text
