from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel

class QuestionOption(BaseModel):
    letter: str
    text: str
    is_other: bool = False
    recommended: bool = False

class Question(BaseModel):
    number: int
    category: Optional[str] = None
    text: str
    options: list[QuestionOption]
    answer: Optional[str] = None

class QuestionFile(BaseModel):
    name: str
    preamble: Optional[str] = None
    questions: list[Question]
    parse_ok: bool
    raw_markdown: Optional[str] = None

class StageState(BaseModel):
    name: str
    status: Literal["pending", "in_progress", "completed"]
    note: Optional[str] = None

class ProjectState(BaseModel):
    project_type: Optional[str] = None
    current_stage: Optional[str] = None
    stages: list[StageState]

class AuditEntry(BaseModel):
    index: int
    timestamp: str
    user_input: str
    ai_response: str
    context: Optional[str] = None
