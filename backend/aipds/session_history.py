# backend/aipds/session_history.py
"""S3 session transcript → chat history conversion.

Location: `discovery/transcript/{session}/main/NNNNNNNN.jsonl`
          (mirrored by DiscoverySessionStore in agent/session_store.py)
Format:   CLI transcript = Anthropic Messages shape
          {"type":"assistant","message":{"role":...,"content":[{"type":"text"|
           "tool_use"|"tool_result", ...}]}}
          Lines without a message (queue-operation, attachment, ai-title, etc.) are mixed in
          too.

**Only one format is read.** Previously the Bedrock Converse shape of the strands fallback
driver (`session_{pid}/agents/agent_default/messages/message_N.json`, with block keys
`toolUse`/`toolResult`) was read as well. When that driver was deleted, the reader was
removed too — sessions left in that format get an empty history list (they were only test
sessions).

The session store is infrastructure outside the sandbox abstraction, so this module reads it
directly rather than through a Sandbox method.
"""
from __future__ import annotations
import json
import logging
from aipds.agent.answer_store import load_answers
from aipds.agent.prompts import shown_text
from aipds.agent.questions_payload import (normalize_sdk_questions,
                                                question_file_from_sdk)
from aipds.agent.session_store import load_transcript
from aipds.models import HistoryItem, HistoryTraceEntry
from aipds.parsers.redaction import redact_credentials
from aipds.tool_trace import tool_detail
from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

def _parse_answers(raw: str) -> dict[str, str] | None:
    """Body with the prefix stripped → answer dict, or None if it cannot be unpacked.

    None is a free-form answer (not JSON). In that case the caller fills only the text
    fallback — the frontend must be able to draw the bubble even without a dict.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    # Values that are not strings (e.g., the agent puts in a number) are also normalized to
    # strings — this keeps the frontend's Record<string, string> contract.
    return {str(k): str(v) for k, v in parsed.items()}


def _answer_fallback_text(body: str, answers: dict[str, str] | None) -> str:
    """Korean fallback text for consumers that do not know about answers.

    The final human-readable text is built by the frontend in the UI language
    (HistoryItem.answers). The Korean left here is only a safety net so that an old frontend
    that does not know that field does not show an empty bubble — new frontends ignore this
    value.
    """
    if answers:
        pretty = " · ".join(f"{k}: {v}" for k, v in
                            sorted(answers.items(), key=lambda kv: str(kv[0])))
        return f"답변 제출 — {pretty}"
    return f"답변 제출: {body}"

#: Tools represented as file_changed in live events (the same set as
#: claude_driver._FILE_TOOLS). History must use the same representation so the scrollback does
#: not look different from live.
_CLI_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}


def _cli_answer_summary(content: object) -> tuple[str, dict[str, str] | None]:
    """ask_questions tool_result body → (fallback text, answers dict or None).

    The body the CLI writes is an English sentence of its own making (answer_store.py header),
    so the answer cannot be unpacked here — `answer_records` provides the exact values. This
    function is the fallback for old sessions that have no records. Note that the return
    value is a tuple: the caller fills both text and answers of HistoryItem.
    """
    if isinstance(content, list):
        inner = "".join(c.get("text", "") for c in content
                        if isinstance(c, dict))
    else:
        inner = str(content or "")
    answers = _parse_answers(inner)
    body = inner
    return _answer_fallback_text(body, answers), answers


def _is_error_result(block: dict) -> bool:
    """Whether this tool_result is a failure.

    A round the CLI blocked by schema validation comes as `is_error: true` with a
    `<tool_use_error>` body (measured: 3 cases where the model passed `questions` as a JSON
    string). Since **the question was never even shown to the user** in that round, creating a
    card on restore would leave a ghost question card nobody saw. The model reads that error
    and retries properly on the next turn, so the screen should have only the retried round.
    """
    if block.get("is_error") is True:
        return True
    content = block.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return "<tool_use_error>" in text


def _is_injected(line: dict) -> bool:
    """Whether this is a line the CLI **inserted** into the conversation — not something the
    user said.

    There are two kinds of lines that come with the user role but were never written by a
    person:
      isMeta            Skill body injection. When the model calls the Skill tool, the CLI
                        inserts the full skill text, starting with "Base directory for this
                        skill: …", as user text (measured: prototype build transcript,
                        shadcn-design skill).
      isCompactSummary  The summary after context compaction. "This session is being
                        continued from a previous conversation…"
    If not filtered out, both become user bubbles, and since they are not real turn boundaries
    they split one turn's bubble in two.
    """
    return line.get("isMeta") is True or line.get("isCompactSummary") is True


def _sdk_questions_to_file(raw_input: object,
                           name: str = "discovery-questions") -> dict | None:
    """tool_use.input → frontend QuestionFile. None on failure (does not block the restore).

    Uses the very function that built the card live — because letter assignment and adding
    the Other option must match what the user actually saw. A question with no options at all
    is a ValueError, and in that case it degrades to the former display without a card name.
    """
    if not isinstance(raw_input, dict):
        return None
    questions = normalize_sdk_questions(raw_input.get("questions"))
    if not questions:
        return None
    try:
        return question_file_from_sdk(questions, name=name)
    except ValueError:
        return None


def transform_cli_transcript(raw: list[dict], *,
                             answer_records: dict[str, dict] | None = None,
                             question_name: str = "discovery-questions",
                             ) -> list[HistoryItem]:
    """CLI transcript (Anthropic Messages shape) → chat history.

    The goal is to produce the same representation as the live stream (claude_driver._translate)
    — text as bubbles, tool runs as traces, AskUserQuestion as cards. That way the scrollback
    does not look different from the screen just seen.

    Lines without a message (queue-operation, attachment, ai-title, last-prompt, etc.) are
    skipped. They are the CLI's internal bookkeeping, not conversation content.

    **One turn is one bubble.** The CLI writes a separate assistant line for each tool call
    (a measured turn with 5 tool uses: 1 thinking line + 5 tool_use lines + 1 text line), so
    creating an item per line splits a turn that was one bubble live into 7, most of them
    empty bubbles with no text — on screen, empty gray boxes with only "추론 과정" (reasoning)
    attached appear one after another. Live creates one AiItem per turn, accumulates message
    into that one, and stacks tools onto the same item's trace
    (useTurnStream.ts:108,121-123), so the restore gathers them the same way.

    The turn boundary is **an actual user utterance**. A user line holding only a `tool_result`
    is a tool execution result, not something the user said, and live renders nothing for that
    line — treating it as a boundary would make each tool call a turn again, back to the
    original problem.

    `question_name` is the name of the restored question card. It must match the name given by
    whoever built the card live — "discovery-questions" for Discovery, "prototype-questions" for
    the prototype builder (_on_can_use_tool in proto/builder.py).
    """
    records = answer_records or {}
    raw = [m for m in raw if not _is_injected(m)]
    # Pass 1: collect AskUserQuestion tool_use ids. The real transcript has other
    # tool_results such as Write/Read mixed in, so id matching is required to pick out only
    # the answer results.
    #
    # The same pass collects two more things:
    #   ask_files   id → that round's question payload. The SDK original remains structured in
    #               tool_use.input (it is not prose), so the card can restore "what it
    #               asked". Previously this was discarded and the card was
    #               degraded to name=None.
    #   errored     Rounds the CLI blocked. No card is created (_is_error_result).
    ask_ids: set[str] = set()
    ask_files: dict[str, dict] = {}
    errored: set[str] = set()
    for m in raw:
        msg = m.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if (block.get("type") == "tool_use"
                    and block.get("name") == "AskUserQuestion"):
                tid = str(block.get("id", ""))
                ask_ids.add(tid)
                qfile = _sdk_questions_to_file(block.get("input"), question_name)
                if qfile is not None:
                    ask_files[tid] = qfile
            elif block.get("type") == "tool_result" and _is_error_result(block):
                errored.add(str(block.get("tool_use_id", "")))

    items: list[HistoryItem] = []
    # The assistant turn in progress. It is finalized as a single item when an actual user
    # utterance is encountered or the transcript ends.
    turn_texts: list[str] = []
    turn_trace: list[HistoryTraceEntry] = []
    turn_cards: list[HistoryItem] = []

    def flush_turn() -> None:
        """Finalize the gathered assistant turn into one item (+ the cards that follow).

        A bubble is created even if there is no text and only a trace — an interrupted turn
        (idle timeout, SSE drop) has that shape, and how far the tools ran is the only record
        that will remain in the scrollback. The progress indicator that was in that spot live
        is not something to restore.

        Cards go **after** the bubble. Live, the model first streams an explanation of "why it
        is asking" and then the question card appears (claude_driver's _CONTACT_ADDENDUM
        requires that explanation), so emitting the card immediately would flip the order while
        the bubble's finalization is deferred, and the question would appear first without an
        explanation.
        """
        nonlocal turn_texts, turn_trace, turn_cards
        if turn_texts or turn_trace:
            items.append(HistoryItem(
                role="ai",
                text=redact_credentials("\n".join(turn_texts)) if turn_texts else "",
                trace=turn_trace))
        items.extend(turn_cards)
        turn_texts, turn_trace, turn_cards = [], [], []

    for m in raw:
        msg = m.get("message")
        if not isinstance(msg, dict):
            continue  # bookkeeping line — no conversation to restore
        role = msg.get("role")
        content = msg.get("content")
        # The content of the first user line may be a plain string rather than a list (measured:
        # {"role":"user","content":"Say OK"}). Normalize it so the block loop does not walk the
        # string character by character.
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue

        texts: list[str] = []
        cards: list[HistoryItem] = []
        trace: list[HistoryTraceEntry] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if text:
                    texts.append(str(text))
            elif btype == "tool_use":
                name = str(block.get("name", ""))
                if name == "AskUserQuestion":
                    # Live, this was a question card. The question payload is sent
                    # along, so the card can show what it asked.
                    #
                    # Rounds the CLI blocked do not create a card — the question was never
                    # shown to the user, and the round the model retried on the next turn
                    # creates its own card (that is what the user actually saw).
                    tid = str(block.get("id", ""))
                    if tid in errored:
                        continue
                    qfile = ask_files.get(tid)
                    cards.append(HistoryItem(
                        role="card", card="questions",
                        name=(qfile or {}).get("name"),
                        questions=qfile))
                elif name in _CLI_FILE_TOOLS:
                    inp = block.get("input")
                    path = ""
                    if isinstance(inp, dict):
                        path = str(inp.get("file_path", ""))
                    trace.append(HistoryTraceEntry(kind="file_changed",
                                                   path=path))
                else:
                    # Read/Glob/Grep/Bash/mcp__aipds__*, etc. — this must be **the same
                    # representation** as the live status event. So detail is also extracted by
                    # the same module (tool_trace): if only one side shows details, the screen
                    # differs before and after a refresh.
                    detail = tool_detail(name, block.get("input"))
                    trace.append(HistoryTraceEntry(
                        kind="status", text=name,
                        detail=redact_credentials(detail) if detail else None))
            elif btype == "tool_result":
                tid = str(block.get("tool_use_id", ""))
                if tid in ask_ids and tid not in errored:
                    # What the user actually answered — the in-progress assistant turn must
                    # be closed first for the order to be right (answer bubble after the
                    # question card).
                    flush_turn()
                    # If there is an answer record, it is the truth (agent/answer_store.py):
                    # the question-number → letter/elaboration map exactly as we received
                    # it, plus the question payload at that moment. Then the frontend builds
                    # the same text with **the same function** as live. Only sessions
                    # without records (before this feature) degrade to the English sentence
                    # the CLI wrote — the reason not to re-parse it is that it becomes
                    # inherently ambiguous once the question text contains quotation marks
                    # (there is a measured case).
                    rec = records.get(tid)
                    if rec is not None:
                        answers = {str(k): str(v) for k, v in rec["answers"].items()}
                        items.append(HistoryItem(
                            role="user",
                            text=redact_credentials(
                                _answer_fallback_text("", answers)),
                            answers=answers,
                            questions=rec.get("questions")))
                    else:
                        text, answers = _cli_answer_summary(block.get("content"))
                        items.append(HistoryItem(role="user",
                                                 text=redact_credentials(text),
                                                 answers=answers,
                                                 questions=ask_files.get(tid)))
            # thinking/redacted_thinking, etc. are omitted — not content to restore and show.

        if role == "assistant":
            # Accumulate into the turn. Finalization is deferred to the next user utterance
            # (or the end of the transcript) so the separate lines that come per tool call
            # do not each become a bubble.
            turn_texts.extend(texts)
            turn_trace.extend(trace)
            turn_cards.extend(cards)
            continue

        # User line. Only an actual utterance is a turn boundary — a line holding only a
        # tool_result is a tool execution result, and live does not render it (the tool_result
        # branch above has already created the answer bubble).
        if texts:
            flush_turn()
            # Only the person's part of a turn text the backend built — the same as the live bubble.
            items.append(HistoryItem(role="user",
                                     text=redact_credentials(shown_text("\n".join(texts)))))
        items.extend(cards)

    flush_turn()
    return items


async def list_history(project_s3: S3StoreLike | None,
                       session_id: str) -> list[HistoryItem]:
    """Read the session transcript and convert it. Any failure degrades to an empty list
    (history is auxiliary data — it does not block the screen).

    Why there is only one store: the strands fallback driver was deleted, so the only prefix to
    read is `projects/{pid}/`. Previously it took two stores and decided which one had the
    content.

    If project_s3 is None, the list is empty (store creation failed — the route wraps it).
    """
    if project_s3 is None:
        return []
    try:
        cli_raw = await load_transcript(project_s3, session_id)
        if not cli_raw:
            return []
        # The answer records live under the same project prefix as the transcript. load_answers
        # degrades failures to an empty dict, so the history is restored via the same path as an
        # old session without records.
        records = await load_answers(project_s3)
        return transform_cli_transcript(cli_raw, answer_records=records)
    except Exception:
        _log.exception("transcript read failed for %s", session_id)
        return []
