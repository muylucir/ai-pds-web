from __future__ import annotations
from typing import Literal
from pydantic import BaseModel

class QuestionOption(BaseModel):
    letter: str
    text: str
    is_other: bool = False
    recommended: bool = False

class Question(BaseModel):
    number: int
    category: str | None = None
    #: The whole body before the options (background prose included). This is the value
    #: shown on screen.
    text: str
    #: The **last paragraph** of it -- the sentence that actually asks.
    #:
    #: Why it is kept separate: the file holds background prose together with the
    #: question, while only the question sentence goes to AskUserQuestion. If the answer
    #: write-back compared `text` (~200 chars) against the tool's question (~22 chars), the
    #: length difference would collapse the similarity -- that is how keumkang-v5's
    #: design-context.md Q4 was lost on 2026-08-16 (0.3721). With a single paragraph this
    #: equals text.
    ask: str = ""
    #: The prose that sat **between** the category header and this question's header --
    #: "why this is being asked".
    #:
    #: How it differs from `text`: `text` is the body *after* the question header, while
    #: this is the explanation *before* it. Upstream's clarification question template
    #: ("Creating Clarification Questions" in question-format-guide.md) writes the grounds
    #: for the ambiguity in that position. In test-wf on 2026-08-17, ~470 of
    #: `pain-point-clarification-questions.md`'s 1,350 characters were this prose, and the
    #: parser put it nowhere, so it disappeared.
    #:
    #: Why it is not merged into `text`: the same 0.3721 incident recorded in the `ask`
    #: comment -- making `text` longer makes that comparison worse. In most files this is
    #: an empty string (the form where the question follows the header directly).
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

class ProjectState(BaseModel):
    project_type: str | None = None
    current_stage: str | None = None
    stages: list[StageState]

class AuditEntry(BaseModel):
    index: int
    timestamp: str
    user_input: str
    ai_response: str
    context: str | None = None

class HistoryTraceEntry(BaseModel):
    """The restore-side trace corresponding to a live AgentEvent's status/file_changed --
    the minimum shape consumed by the "reasoning" accordion in the frontend's AiMessage."""
    kind: Literal["status", "file_changed"]
    text: str | None = None
    path: str | None = None
    #: **What the tool did** (the file read, the command run, ...). Live it arrives as the
    #: status event's payload and here it is a field -- the place that builds the value is
    #: one (tool_trace).
    detail: str | None = None


class HistoryItem(BaseModel):
    role: Literal["user", "ai", "card"]
    text: str | None = None
    card: Literal["questions"] | None = None
    name: str | None = None
    # For role=="ai", that turn's tool execution trace (an empty list when there is none)
    trace: list[HistoryTraceEntry] = []
    # An answer-submission turn's structured answers ({"1": "A", "2": "B,C"}). The wording
    # a human reads is built by the frontend in the UI language -- the backend does not know
    # the UI language. A free-prose answer that is not JSON cannot be unpacked into a dict
    # and is None; then only text is used.
    answers: dict[str, str] | None = None
    # That round's question payload (a QuestionFile). When present, the frontend builds the
    # wording with the same answerSummary() as live -- the question numbers, option letters
    # and option texts come from here. It is carried on role=="card" too, to restore "what
    # was asked".
    questions: dict | None = None

class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error",
                  # The declaration that Discovery has handed the prototype over to the
                  # build (agent/reconcile.py derives it from the write of
                  # build-instructions.md). The frontend draws the "go to the Prototypes
                  # tab" card from this event -- the user has to be left somewhere to click
                  # even if the agent forgets to say so.
                  "prototype_ready",
                  # A prototype build's explicit completion declaration
                  # (proto/tools.py). This event ends the session's life --
                  # proto/session.py observes it and moves status to "complete".
                  "build_complete"]
    text: str | None = None
    path: str | None = None
    # Structured payload (JSON string) for questions/stage/document — the
    # event IS the UI contract; files stay as records only.
    payload: str | None = None

class TurnResult(BaseModel):
    events: list[AgentEvent]
