from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, computed_field

class QuestionOption(BaseModel):
    letter: str
    text: str
    is_other: bool = False
    recommended: bool = False

class Question(BaseModel):
    number: int
    category: str | None = None
    #: The whole body before the options (including background prose). This is the value shown
    #: on screen.
    text: str
    #: Its **last paragraph** — the sentence that actually asks.
    #:
    #: Why split them: the file holds background prose + the question together, but only the
    #: question sentence goes to AskUserQuestion. If the answer write-back compared `text`
    #: (~200 characters) with the tool's question (~22 characters), the length difference would
    #: collapse the similarity — that is how Q4 of design-context.md in ws-a-v5 was lost on
    #: 2026-08-16 (0.3721). With a single paragraph it equals text.
    ask: str = ""
    #: The prose that was **between** the category header and this question's header — "why this
    #: is being asked".
    #:
    #: How it differs from `text`: `text` is the body *after* the question header, and this is the
    #: explanation *before* it. The upstream clarification question template ("Creating
    #: Clarification Questions" in question-format-guide.md) writes the basis for the ambiguity in
    #: that spot. In test-wf on 2026-08-17, ~470 of the 1,350 characters of
    #: `pain-point-clarification-questions.md` were this prose, and the parser stored it nowhere,
    #: so it vanished.
    #:
    #: Why it is not merged into `text`: same as the 0.3721 incident in the `ask` comment — making
    #: `text` longer makes that comparison worse. In most files it is an empty string (the form
    #: where the question comes right after the question header).
    context: str = ""
    options: list[QuestionOption]
    answer: str | None = None
    multi_select: bool = False

class QuestionFile(BaseModel):
    name: str
    preamble: str | None = None
    questions: list[Question]
    parse_ok: bool
    raw_markdown: str | None = None

class StageState(BaseModel):
    name: str
    status: Literal["pending", "in_progress", "completed"]
    note: str | None = None

class CapabilityState(BaseModel):
    key: str
    name: str
    status: Literal["pending", "in_progress", "completed", "not_applicable"]
    note: str | None = None

class ProjectState(BaseModel):
    project_type: str | None = None
    current_stage: str | None = None
    stages: list[StageState]

    #: The unit the screen counts. `stages` is the original list the agent wrote
    #: (aipds/capabilities.py).
    @computed_field
    @property
    def capabilities(self) -> list[CapabilityState]:
        from aipds.capabilities import rollup
        return rollup(self.stages)

class AuditEntry(BaseModel):
    index: int
    timestamp: str
    user_input: str
    ai_response: str
    context: str | None = None

class HistoryTraceEntry(BaseModel):
    """The restore trace corresponding to the live AgentEvent's status/file_changed — the minimal
    shape consumed by the frontend AiMessage's "추론 과정" (reasoning) accordion."""
    kind: Literal["status", "file_changed"]
    text: str | None = None
    path: str | None = None
    #: **What the tool did** (the file read, the command run…). Live, it comes as the status
    #: event's payload, and here it is a field — the value is produced in one place
    #: (tool_trace).
    detail: str | None = None


class HistoryItem(BaseModel):
    role: Literal["user", "ai", "card"]
    text: str | None = None
    card: Literal["questions"] | None = None
    name: str | None = None
    # When role=="ai", that turn's tool execution trace (empty list if none)
    trace: list[HistoryTraceEntry] = []
    # The structured answers of an answer-submission turn ({"1": "A", "2": "B,C"}). The
    # human-readable text is built by the frontend in the UI language — the backend does not
    # know the UI language. A free-form answer that is not JSON cannot be unpacked into a
    # dict, so it is None, and then only text is used.
    answers: dict[str, str] | None = None
    # That round's question payload (QuestionFile). If present, the frontend builds the text with
    # the same answerSummary() as live — question numbers, option letters, and option text come
    # from here. It is also carried for role=="card" to restore "what was asked".
    questions: dict | None = None

class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error",
                  # The declaration that Discovery has handed the prototype over to build
                  # (agent/reconcile.py derives it from the write of build-instructions.md).
                  # The frontend draws the "Prototypes 탭으로 가기" (go to the Prototypes tab)
                  # card from this event — even if the agent forgets the guidance sentence,
                  # the user must still have somewhere to click.
                  "prototype_ready",
                  # The explicit completion declaration of a prototype build (proto/tools.py).
                  # This event ends the session's lifetime — proto/session.py observes it and
                  # changes status to "complete".
                  "build_complete",
                  # One sub-agent's progress. The only reason it is separate from `status` is
                  # that the payload says **which** agent it belongs to (`task_id`), and that one
                  # thing lets parallel work be drawn as several rows instead of one line — the
                  # rationale is in the agent_activity.py header.
                  "agent_activity",
                  # A record that the user answered a question in a prototype build. The build
                  # turn continues past the question without breaking (proto/builder.py), so the
                  # answer is an event **inside** the turn, and it must be in the turn log for a
                  # reattached screen to restore the question card and answer bubble in order.
                  # payload = {"answers": {...}}.
                  "answers",
                  # The main agent's context window usage (aipds/context_usage.py). payload =
                  # {total_tokens, max_tokens, compact_at_tokens, left_pct}. Flows only when
                  # the value changes — read by the screen's "남은 %" (% left) display.
                  "context",
                  # The file the model is writing right now (agent/tool_input_draft.py). While
                  # the tool input streams, it carries what is being written and how many
                  # characters in. payload = {id, tool, state, chars}. Exists only in the live
                  # stream — it is left in neither the transcript nor the source of truth.
                  "draft"]
    text: str | None = None
    path: str | None = None
    # Structured payload (JSON string) for questions/stage/document — the
    # event IS the UI contract; files stay as records only.
    payload: str | None = None
