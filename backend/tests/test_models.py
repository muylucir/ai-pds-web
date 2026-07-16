from pathfinder.models import Question, QuestionOption, QuestionFile

def test_question_file_roundtrips_multiselect_answer():
    q = Question(
        number=12, category="Success Metrics", text="핵심 KPI는?",
        options=[
            QuestionOption(letter="A", text="시간 절감", is_other=False, recommended=True),
            QuestionOption(letter="X", text="Other", is_other=True, recommended=False),
        ],
        answer="A,B",
    )
    qf = QuestionFile(name="strategy-questions.md", preamble=None,
                      questions=[q], parse_ok=True, raw_markdown=None)
    assert qf.questions[0].answer == "A,B"
    assert qf.questions[0].options[0].recommended is True
