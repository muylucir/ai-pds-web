# backend/aipds/survey/models.py — validation-survey wire/storage models.
from __future__ import annotations
from typing import Literal, Union
from pydantic import BaseModel, Field, model_validator

QuestionType = Literal["scale", "choice", "text"]
SurveyStatus = Literal["open", "closed"]

#: scale is a fixed 1-5 integer range; the public form renders it as such.
SCALE_MIN = 1
SCALE_MAX = 5


class Question(BaseModel):
    id: str
    text: str
    type: QuestionType
    options: list[str] = Field(default_factory=list)
    required: bool = True
    # The question that asks which of the spec's Target Users the respondent is. The
    # rollup splits the other questions by this answer (store._aggregate_markdown). The
    # generator creates this question itself; model-generated questions never have it.
    # Surveys created before this field existed lack it.
    persona: bool = False

    @model_validator(mode="after")
    def _options_match_type(self) -> "Question":
        if self.type == "choice" and len(self.options) < 2:
            raise ValueError("choice question needs at least 2 options")
        if self.type in ("scale", "text") and self.options:
            raise ValueError(f"{self.type} question must not carry options")
        if self.persona and self.type != "choice":
            raise ValueError("persona question must be a choice question")
        return self


class Questionnaire(BaseModel):
    token: str
    status: SurveyStatus
    slug: str
    project_id: str
    created_at: str
    closed_at: str | None = None
    # The language the questions are written in ("ko"|"en"). The public response page
    # picks its on-screen text by this value. Existing surveys lack it, so a default is
    # required — all of those surveys were created in Korean.
    language: str = "ko"
    title: str
    hypothesis: str
    questions: list[Question]

    @model_validator(mode="after")
    def _questions_sane(self) -> "Questionnaire":
        if not self.questions:
            raise ValueError("questionnaire needs at least one question")
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            # Duplicate ids collapse in the answers dict, silently dropping a
            # question's responses.
            raise ValueError("question ids must be unique")
        if sum(q.persona for q in self.questions) > 1:
            # There must be exactly one criterion that splits the responses.
            raise ValueError("at most one persona question")
        return self


class SurveyResponse(BaseModel):
    response_id: str
    submitted_at: str
    answers: dict[str, Union[str, int]]


class ScaleStat(BaseModel):
    type: Literal["scale"] = "scale"
    n: int
    mean: float
    distribution: dict[str, int]


class ChoiceStat(BaseModel):
    type: Literal["choice"] = "choice"
    n: int
    counts: dict[str, int]


class TextStat(BaseModel):
    type: Literal["text"] = "text"
    n: int
    samples: list[str]


Stat = Union[ScaleStat, ChoiceStat, TextStat]


class Rollup(BaseModel):
    count: int
    rebuilt_at: str
    per_question: dict[str, Stat]
