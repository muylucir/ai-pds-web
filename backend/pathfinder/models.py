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
    text: str
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

class HistoryItem(BaseModel):
    role: Literal["user", "ai", "card"]
    text: str | None = None
    card: Literal["questions"] | None = None
    name: str | None = None
