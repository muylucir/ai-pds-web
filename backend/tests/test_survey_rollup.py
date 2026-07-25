from pathfinder.survey.models import Question, SurveyResponse
from pathfinder.survey.rollup import build_rollup, TEXT_SAMPLE_LIMIT

NOW = "2026-07-25T00:00:00Z"

QUESTIONS = [
    Question(id="q1", text="유용?", type="scale"),
    Question(id="q2", text="어느 것?", type="choice", options=["A", "B"]),
    Question(id="q3", text="자유", type="text", required=False),
]


def _r(rid, answers):
    return SurveyResponse(response_id=rid, submitted_at=NOW, answers=answers)


def test_scale_mean_and_distribution():
    responses = [_r("1", {"q1": 4}), _r("2", {"q1": 5}), _r("3", {"q1": 4})]
    ru = build_rollup(QUESTIONS, responses, NOW)
    stat = ru.per_question["q1"]
    assert stat.n == 3
    assert stat.mean == round((4 + 5 + 4) / 3, 2)
    assert stat.distribution == {"1": 0, "2": 0, "3": 0, "4": 2, "5": 1}
    assert ru.count == 3


def test_choice_counts_include_zero_options():
    # An option nobody picked must still appear as 0 -- otherwise the dashboard
    # silently hides the fact that it was offered.
    responses = [_r("1", {"q2": "A"}), _r("2", {"q2": "A"})]
    stat = build_rollup(QUESTIONS, responses, NOW).per_question["q2"]
    assert stat.counts == {"A": 2, "B": 0}
    assert stat.n == 2


def test_text_samples_capped_and_blank_skipped():
    responses = [_r(str(i), {"q3": f"의견 {i}"}) for i in range(30)]
    responses.append(_r("blank", {"q3": "   "}))
    stat = build_rollup(QUESTIONS, responses, NOW).per_question["q3"]
    assert stat.n == 30              # blank not counted
    assert len(stat.samples) == TEXT_SAMPLE_LIMIT


def test_missing_optional_answers_are_not_counted():
    responses = [_r("1", {"q1": 3}), _r("2", {})]
    ru = build_rollup(QUESTIONS, responses, NOW)
    assert ru.count == 2             # both responses exist
    assert ru.per_question["q1"].n == 1
    assert ru.per_question["q3"].n == 0


def test_out_of_range_and_non_numeric_scale_ignored():
    # A hand-crafted POST could carry 9 or "abc"; the aggregate must not skew
    # or crash on it.
    responses = [_r("1", {"q1": 4}), _r("2", {"q1": 9}), _r("3", {"q1": "abc"})]
    stat = build_rollup(QUESTIONS, responses, NOW).per_question["q1"]
    assert stat.n == 1
    assert stat.mean == 4.0


def test_unknown_answer_keys_ignored():
    responses = [_r("1", {"q1": 4, "qZ": "몰래"})]
    ru = build_rollup(QUESTIONS, responses, NOW)
    assert set(ru.per_question) == {"q1", "q2", "q3"}


def test_empty_responses_yield_zeroed_stats():
    ru = build_rollup(QUESTIONS, [], NOW)
    assert ru.count == 0
    assert ru.per_question["q1"].n == 0 and ru.per_question["q1"].mean == 0.0
    assert ru.per_question["q2"].counts == {"A": 0, "B": 0}
    assert ru.per_question["q3"].samples == []
