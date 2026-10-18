# Whether ClaudeDriver passes, unchanged, the contract settled with StrandsDriver in Task 3.
# If it passes, runner.py and the frontend cannot tell the two drivers apart (= functional parity).
#
# Script translation lives in tests/fakes/fake_sdk_asking.py — the existing FakeSdkClient is
# not extended (builder tests depend on its shape). In particular, questions/
# followup_questions are mimicked with an AskingSdkClient that, like the real SDK, calls the
# driver's can_use_tool callback in a separate task while receive_response() emits nothing in
# the meantime — because that callback is the only path that produces the questions event.
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _legacy_question_path(monkeypatch):
    """This contract belongs to the **AskUserQuestion path** — `run_answers(interrupt_id, …)` is
    that path's interface. When the default question path switched to question files on 2026-08-17,
    this path became the escape hatch (claude_driver.FILE_QUESTIONS_ENV), and since the escape hatch must stay
    alive, this contract must stay alive too. It is turned off explicitly rather than relying on the default.
    """
    monkeypatch.setenv("AIPDS_FILE_QUESTIONS", "false")

from aipds.agent.claude_driver import ClaudeDriver
from aipds.agent.pending_store import PENDING_KEY
from tests.driver_contract import assert_driver_contract
from tests.fakes.fake_sdk_asking import cancel_pending_callbacks, sdk_client_for
from tests.fakes.in_memory_s3 import FakeS3Store

# The interrupt_id the contract passes to run_answers (driver_contract.py:128,154).
_CONTRACT_IID = "i-42"
# The question the restart path translates back. Written together into the record planted in S3.
_SEEDED_SDK_QUESTIONS = [{"question": "다음 단계는?",
                          "options": [{"label": "진행"}, {"label": "종료"}]}]


@pytest.fixture(autouse=True)
def _cleanup_parked_callbacks():
    """The questions script ends without answers, so a parked can_use_tool task remains —
    it leaks out as "Task was destroyed but it is pending!" when the loop closes."""
    yield
    cancel_pending_callbacks()


def _seed_restart_pending(s3: FakeS3Store) -> None:
    """Setup for scripts that exercise only run_answers (echo_answers/followup_questions).

    The contract calls run_answers right after getting a new driver from make_driver — with no
    in-memory future, the driver takes the "backend restart" path (_resume_with_answers).
    That path builds the prompt from the saved sdk_questions, so a pending record must exist
    in S3. In other words, this seed is not a workaround but a reproduction of the restart state.

    The event loop is already running (pytest-asyncio), so save_pending cannot be called
    without await, and the same shape is written directly into FakeS3Store's synchronous text view.

    The interrupt_id must equal the value the contract passes ("i-42", driver_contract.py:128,154) —
    the driver now validates the round on both paths, so planting a different value would be
    rightly rejected. So echo honesty (received value vs. stored value) cannot be
    proven here, and a dedicated test with differing values,
    test_claude_driver.py::test_the_answer_record_echoes_the_received_values,
    covers it.
    """
    s3.blobs[PENDING_KEY] = json.dumps({
        "interrupt_id": _CONTRACT_IID,
        "questions": {"name": "q", "questions": []},
        "sdk_questions": _SEEDED_SDK_QUESTIONS,
        "session_id": "s-1",
    }, ensure_ascii=False)


def _make_claude_driver(scripted: dict, tmp_path_factory=None):
    import tempfile
    from pathlib import Path

    ws = tempfile.mkdtemp()
    rules = tempfile.mkdtemp()
    # The minimal layout place_rules requires.
    core = Path(rules) / "aws-aiplc-rules"
    core.mkdir(parents=True)
    (core / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    # The language directive is not in this fixture — it comes from code
    # (agent/workspace_rules.LANGUAGE_DIRECTIVES), not `rules_dir`, so it is not
    # part of the minimal layout.

    s3 = FakeS3Store()
    driver = ClaudeDriver(workspace=ws, rules_dir=rules,
                          config_dir=tempfile.mkdtemp(), s3=s3,
                          client_factory=lambda session: None)

    def factory(session):
        # Plug the driver's own _on_can_use_tool into the same place the real factory wires it
        # (ClaudeAgentOptions(can_use_tool=...)).
        return sdk_client_for(scripted, driver._on_can_use_tool)

    driver._client_factory = factory  # type: ignore[assignment]

    if scripted.get("echo_answers") or scripted.get("followup_questions"):
        _seed_restart_pending(s3)

    return driver, {"session_id": "s-1"}


@pytest.mark.asyncio
async def test_claude_driver_satisfies_the_same_contract():
    await assert_driver_contract(_make_claude_driver)
