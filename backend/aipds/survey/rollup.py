# backend/aipds/survey/rollup.py — pure aggregation: responses -> Rollup.
#
# Kept free of S3/HTTP so the dashboard's numbers are unit-testable, and so a
# rollup rebuild is a pure re-derivation from the response objects (which are
# the source of truth -- the stored rollup is only a cache).
from __future__ import annotations

from typing import Sequence

from aipds.survey.models import (ChoiceStat, Question, Rollup, ScaleStat,
                                      SCALE_MAX, SCALE_MIN, SurveyResponse,
                                      TextStat)

#: Cap on text answers kept in the rollup. The full text lives in the response
#: objects and is exported via CSV; an uncapped rollup would grow without bound.
TEXT_SAMPLE_LIMIT = 20


def _scale_stat(values: Sequence[object]) -> ScaleStat:
    dist = {str(i): 0 for i in range(SCALE_MIN, SCALE_MAX + 1)}
    nums: list[int] = []
    for v in values:
        # bool is an int subclass but is never a valid scale answer.
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if SCALE_MIN <= v <= SCALE_MAX:
            nums.append(v)
            dist[str(v)] += 1
    mean = round(sum(nums) / len(nums), 2) if nums else 0.0
    return ScaleStat(n=len(nums), mean=mean, distribution=dist)


def _choice_stat(values: Sequence[object], options: list[str]) -> ChoiceStat:
    # Seed every offered option at 0 so the dashboard shows unchosen options.
    counts = {opt: 0 for opt in options}
    n = 0
    for v in values:
        if isinstance(v, str) and v in counts:
            counts[v] += 1
            n += 1
    return ChoiceStat(n=n, counts=counts)


def _text_stat(values: Sequence[object]) -> TextStat:
    texts = [v.strip() for v in values if isinstance(v, str) and v.strip()]
    return TextStat(n=len(texts), samples=texts[:TEXT_SAMPLE_LIMIT])


def build_rollup(questions: list[Question], responses: list[SurveyResponse],
                 now: str) -> Rollup:
    per_question: dict = {}
    for q in questions:
        values = [r.answers[q.id] for r in responses if q.id in r.answers]
        if q.type == "scale":
            per_question[q.id] = _scale_stat(values)
        elif q.type == "choice":
            per_question[q.id] = _choice_stat(values, q.options)
        else:
            per_question[q.id] = _text_stat(values)
    return Rollup(count=len(responses), rebuilt_at=now,
                  per_question=per_question)


def persona_groups(questions: list[Question], responses: list[SurveyResponse]
                   ) -> tuple[Question, list[tuple[str, list[SurveyResponse]]]] | None:
    """페르소나 문항과, 그 답으로 나눈 응답 묶음. 페르소나 문항이 없으면 None이다.

    묶음은 선택지 순서이고 응답이 있는 페르소나만 담는다 — 응답 0인 페르소나는
    페르소나 문항 자신의 집계가 이미 보여 준다. 페르소나 문항은 필수이고 공개
    제출이 선택지 밖의 답을 거절하므로(routes/surveys_public) 어느 묶음에도
    들지 않는 응답은 없다.
    """
    pq = next((q for q in questions if q.persona), None)
    if pq is None:
        return None
    groups = [(opt, [r for r in responses if r.answers.get(pq.id) == opt])
              for opt in pq.options]
    return pq, [(opt, rs) for opt, rs in groups if rs]
