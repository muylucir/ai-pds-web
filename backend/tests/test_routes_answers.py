# backend/tests/test_routes_answers.py
from pathlib import Path
import asyncio
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.app import app, registry
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

def _seed(monkeypatch, pid, language=None):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    body = {"project_id": pid}
    if language is not None:
        body["language"] = language
    client.post("/projects", json=body)
    ws = registry.get(pid)
    # Use asyncio.run (not get_event_loop().run_until_complete) — the latter is
    # deprecated on 3.11 and conflicts with pytest-asyncio's managed loop.
    asyncio.run(
        ws.runner.write_file("aiplc-docs/strategy-questions.md",
            (FIX / "strategy-questions.md").read_text(encoding="utf-8")))

# ---- Submitting answers for a file question round ----
# This path does not return to a parked turn. The PostToolUse hook saw the question file and
# **ended** the turn (claude_driver._on_post_tool_use), so there is no turn to resume — we write
# the answers to the file and call the agent again with a **new turn**. The backend builds that
# new turn's text: it is a sentence the agent reads, so it must follow the project language,
# not the UI language (the rule in agent/prompts.py), and keeping that wording in the frontend
# would make the frontend manage both languages.

def _turn(pid: str) -> dict:
    """Input of the turn the route started — the last text the runner received."""
    sent = registry.get(pid).runner.sent
    assert sent, "the route started no turn"
    return {"text": sent[-1]}


def _submit(pid: str, answers: dict):
    return client.post(
        f"/projects/{pid}/questions/aiplc-docs/strategy-questions.md/answers",
        json={"answers": answers})


def test_submitting_file_answers_writes_them_and_starts_the_resume_turn(monkeypatch):
    _seed(monkeypatch, "fq1")
    r = _submit("fq1", {"1": "B"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Written to the file — it writes by number, so there is no fuzzy matching (serialize_answers).
    by_num = {q["number"]: q["answer"] for q in body["questions"]["questions"]}
    assert by_num[1] == "B"
    # Then it starts the follow-up turn and returns its id. The frontend watches it through the
    # existing GET /events?turn= — we do not add a new stream endpoint.
    assert body["turn_id"]
    assert registry.get("fq1").runner.sent


def test_the_audit_log_gets_what_the_user_chose_not_the_agent_directives(monkeypatch):
    """The turn text appends agent instructions after the answers. The audit log's "user input"
    must be only what the user picked — the same string as the on-screen bubble (summary)
    (aipds/audit_log)."""
    _seed(monkeypatch, "fq-audit")
    body = _submit("fq-audit", {"1": "B"}).json()
    runner = registry.get("fq-audit").runner
    record = runner.records[-1]
    assert record.text == body["summary"]
    assert record.text != runner.sent[-1] and record.text in runner.sent[-1]
    assert (record.source, record.detail) == ("answers", "strategy-questions.md")


def test_the_resume_turn_carries_a_prompt_that_names_the_file(monkeypatch):
    """The resume turn's text is the sentence that goes to the agent.

    If it does not name the file, the agent does not know which file to re-read —
    having several question files is normal (measured: 9 in one project)."""
    _seed(monkeypatch, "fq2")
    handle = _submit("fq2", {"1": "B"}).json()["turn_id"]
    payload = _turn("fq2")
    assert payload is not None
    assert "strategy-questions.md" in payload["text"]


def test_submitting_to_a_missing_file_404(monkeypatch):
    _seed(monkeypatch, "fq3")
    r = client.post("/projects/fq3/questions/aiplc-docs/nope.md/answers",
                    json={"answers": {"1": "B"}})
    assert r.status_code == 404


def test_submitting_an_unknown_question_number_400(monkeypatch):
    """Same contract as PUT — if it silently ignored this, nobody would know the answers were lost."""
    _seed(monkeypatch, "fq4")
    assert _submit("fq4", {"99": "A"}).status_code == 400
    assert _submit("fq4", {"abc": "A"}).status_code == 400


# ---- Pointing at the state file when it is missing ----
# The stage badge is derived from `aiplc-state.md` (agent/reconcile.py). The hook and the
# turn-boundary reconciliation carry that file onto the screen **when it exists**; if the file
# itself is missing there is nothing to carry — and only the agent can create it (only the
# agent knows the stage names; guessing from paths would create a second copy of the ruleset's
# stage names).
#
# The answer-submission turn is where we point this out. On 2026-08-18 in test123456 the first
# turn ended without a state file, and the resume turn says "from where you stopped", so it had
# no trigger to create one on its own.

def _state(pid: str, markdown: str):
    asyncio.run(registry.get(pid).runner.write_file(
        "aiplc-docs/aiplc-state.md", markdown))


def _resume_text(pid: str) -> str:
    handle = _submit(pid, {"1": "B"}).json()["turn_id"]
    payload = _turn(pid)
    assert payload is not None
    return payload["text"]


def test_the_resume_turn_asks_for_the_state_file_when_it_never_landed(
        monkeypatch):
    _seed(monkeypatch, "fq5")  # no aiplc-state.md
    text = _resume_text("fq5")
    assert "aiplc-state.md" in text
    # The file pointer must stay as is — the note does not replace the original prompt.
    assert "strategy-questions.md" in text


def test_a_state_file_without_a_current_stage_still_counts_as_missing(monkeypatch):
    """The check is `current_stage is None`, not the file's absence.

    The format the upstream rules require always has a Current Stage line, so a file
    without that line means there is no readable state — checking only that the file
    exists would miss this case."""
    _seed(monkeypatch, "fq6")
    _state("fq6", "# AI-PLC State\n\n## Stage Progress\n")
    assert "aiplc-state.md" in _resume_text("fq6")


def test_the_resume_turn_stays_quiet_once_a_stage_is_recorded(monkeypatch):
    """A turn that ran in the normal order does not get this note.

    If it were attached every round, the agent would re-declare a stage it already
    declared, and that repetition shakes the checklist."""
    _seed(monkeypatch, "fq7")
    _state("fq7", "# AI-PLC State\n\n- **Current Stage**: Envision\n\n"
                  "## Stage Progress\n- [ ] Envision\n")
    text = _resume_text("fq7")
    assert "aiplc-state.md" not in text
    assert "strategy-questions.md" in text


def test_the_note_follows_the_project_language(monkeypatch):
    """It is a sentence the agent reads, so it is in the project language, not the UI language
    (the convention in the agent/prompts.py header)."""
    _seed(monkeypatch, "fq8", language="en")
    text = _resume_text("fq8")
    assert "aiplc-state.md" in text
    # If Korean gets mixed into an English project's prompt, that project's conversation
    # leaks into Korean — that is the shape of the 2026-08-04 defect.
    #
    # **We do not check "no Korean in the body" (2026-08-21).** This text now quotes
    # sentences from the question file (aipds/answer_summary.py). Those sentences are the
    # project's content, not our prompt, and a real English project's question file is in
    # English — only this fixture is Korean. So we check that the **wrapper** we build is in
    # the project language. The exhaustive prompt check is tests/test_agent_language.py's job.
    for korean_wrapper in ("질문에 답했습니다", "다시 묻지 마세요",
                           "파일을 읽고 멈춘 지점부터"):
        assert korean_wrapper not in text, korean_wrapper
    assert "I answered the questions" in text


def test_a_failing_state_probe_does_not_break_answer_submission(monkeypatch):
    """The user already submitted the form. The note is an auxiliary instruction, so it cannot block the submission."""
    _seed(monkeypatch, "fq9")
    async def boom():
        raise RuntimeError("s3 down")
    monkeypatch.setattr(registry.get("fq9"), "get_state", boom)
    r = _submit("fq9", {"1": "B"})
    assert r.status_code == 200, r.text
    assert r.json()["turn_id"]


# ---- A restored bubble must be what the user actually answered ----
#
# **Measured (2026-08-21).** When the workspace was refreshed and the conversation restored,
# every user bubble was stuck on the same machine wording: "질문에 답했습니다. 답변은 `…-questions.md`의
# `[Answer]:` 태그에 들어 있으니, 파일을 읽고 멈춘 지점부터 이어가 주세요."
# ("I answered the questions. The answers are in the `[Answer]:` tags of `…-questions.md`,
# so please read the file and continue from where you stopped.")
#
# The live screen draws the actual answers (useWorkspaceStream builds them with
# `answerSummary(questions, answers, t)`). So **the same round looked different before and
# after a refresh** — the defect recorded in the `agent/answer_store.py` header came back on
# the file question path. That one was fixed by joining on `tool_use_id`, but a file round has
# no such tool call at all (what the agent calls is `Write`), so there was no id to join on and
# no record.
#
# **We do not join through an opaque marker.** `frontend/lib/approvalMarker.ts` records the
# decision for this class — "this text is not a machine signal. That turn stays in the
# transcript and chat history as a user bubble. The agent must understand it and a person must
# read it." So the turn text itself carries the human-readable answers: the transcript holds
# the truth, so there is no join, no new state, and no ordering dependency.

def test_the_resume_turn_carries_the_answers_the_user_actually_gave(monkeypatch):
    """The text that becomes the bubble must contain the answers — with only a file pointer,
    the restore stops at "질문에 답했습니다" ("I answered the questions")."""
    _seed(monkeypatch, "fqans1")
    handle = _submit("fqans1", {"1": "B", "12": "A,C"}).json()["turn_id"]
    payload = _turn("fqans1")

    text = payload["text"]
    # Both questions' answers arrive **in readable form**. Why we do not assert the letter
    # literal ("A,C"): since rendering moved to the backend on 2026-08-21 they expand to the
    # option labels — the two tests above pin that expansion per question.
    assert "플랫폼(Platform)" in text, text
    assert "신규 MD 온보딩 기간 단축률" in text, text
    # The file pointer stays — the file is still the authority (the `[Answer]:` tag is canonical).
    assert "strategy-questions.md" in text


def test_the_resume_turn_keeps_free_text_answers_verbatim(monkeypatch):
    """A free-text answer must be exactly the sentence the user wrote — if we summarized or
    truncated it, that would become the record of the restored conversation."""
    _seed(monkeypatch, "fqans2")
    written = "예산은 3분기까지 확정되지 않았습니다. 그 전에는 B로 갑니다."
    handle = _submit("fqans2", {"1": written}).json()["turn_id"]
    payload = _turn("fqans2")

    assert written in payload["text"]


def test_the_resume_turn_follows_the_project_language(monkeypatch):
    """It is conversation text that remains as a bubble — the project language, not the UI
    language (`answer_first` and `approvalMarker.ts` record the same judgment)."""
    _seed(monkeypatch, "fqans3", language="en")
    handle = _submit("fqans3", {"1": "B"}).json()["turn_id"]
    text = _turn("fqans3")["text"]

    # Check only the wrapper — the body quotes sentences from the question file, and this
    # fixture is Korean (the reasoning is in the same comment on fq8).
    assert "I answered the questions" in text, text
    assert "질문에 답했습니다" not in text, text
    assert "Q1." in text


# ---- The recorded text must match the screen (2026-08-21) ----
#
# That morning's `fe6a482` put the answers into the turn text, but **as raw option letters**
# ("- 1: A,B"). The live screen expands letters into option labels (at the time,
# frontend/lib/answerSummary.ts), so the record and the screen still differed — only half the
# defect was fixed. Now there is a single backend renderer (aipds/answer_summary.py) and its
# output is the turn text.

def test_the_resume_turn_renders_option_labels_not_bare_letters(monkeypatch):
    """A letter alone means nothing to the reader — if the restored conversation shows only
    "B", you have to reopen the question form to learn what the decision was."""
    _seed(monkeypatch, "lbl1")
    handle = _submit("lbl1", {"1": "B"}).json()["turn_id"]
    text = _turn("lbl1")["text"]

    assert "플랫폼(Platform)" in text, text
    # The question sentence comes along too — with only the answer you cannot tell what it answers.
    assert "포지셔닝" in text, text
    assert "Q1." in text, text


def test_the_resume_turn_expands_every_letter_of_a_multi_select_answer(monkeypatch):
    """A multi-select arrives comma-separated ("A,C"). Recording it as is would leave two decisions as two letters."""
    _seed(monkeypatch, "lbl2")
    handle = _submit("lbl2", {"12": "A,C"}).json()["turn_id"]
    text = _turn("lbl2")["text"]

    assert "MD 업무 시간 절감률" in text, text
    assert "신규 MD 온보딩 기간 단축률" in text, text


def test_the_submit_response_carries_the_same_text_it_recorded(monkeypatch):
    """The basis for the frontend not rebuilding the bubble. What the server records and what
    the frontend shows must be **the same string**; only then can they not diverge."""
    _seed(monkeypatch, "lbl3")
    body = _submit("lbl3", {"1": "B"}).json()
    recorded = _turn("lbl3")["text"]

    assert body["summary"], "응답에 말풍선 텍스트가 없다"
    # The recorded turn text has model instructions appended, so it **contains** the summary.
    assert body["summary"] in recorded, (body["summary"], recorded)
    assert "플랫폼(Platform)" in body["summary"]
