from aipds.models import Question, QuestionOption, QuestionFile

def test_question_multi_select_defaults_false():
    q = Question(number=1, text="누구?", options=[])
    assert q.multi_select is False
    assert Question(number=1, text="누구?", options=[], multi_select=True).multi_select

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

def test_agent_event_lives_in_models_with_full_kind_literal():
    from aipds.models import AgentEvent, TurnResult
    e = AgentEvent(kind="questions", payload='{"interrupt_id":"i-1"}')
    assert e.kind == "questions" and e.text is None and e.path is None
    tr = TurnResult(events=[e, AgentEvent(kind="done")])
    assert [ev.kind for ev in tr.events] == ["questions", "done"]
