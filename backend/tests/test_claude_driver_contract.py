# Task 3에서 StrandsDriver로 확정한 계약을 ClaudeDriver가 그대로 통과하는지.
# 통과하면 runner.py와 프론트가 두 드라이버를 구분하지 못한다(= 기능 동등).
#
# 대본 번역은 tests/fakes/fake_sdk_asking.py에 있다 — 기존 FakeSdkClient는
# 확장하지 않는다(builder 테스트가 그 shape에 의존한다). 특히 questions/
# followup_questions는 실제 SDK처럼 드라이버의 can_use_tool 콜백을 별도 태스크에서
# 호출하고 그 동안 receive_response()가 아무것도 내지 않는 AskingSdkClient로
# 흉내낸다 — 그 콜백이 questions 이벤트를 만드는 유일한 경로이기 때문이다.
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _legacy_question_path(monkeypatch):
    """이 계약은 **AskUserQuestion 경로**의 것이다 — `run_answers(interrupt_id, …)`가
    그 경로의 인터페이스다. 2026-08-17에 기본 질문 경로가 질문 파일로 바뀌면서 이
    경로는 탈출로가 됐고(claude_driver.FILE_QUESTIONS_ENV), 탈출로가 살아 있어야
    하므로 이 계약도 살아 있어야 한다. 기본값에 의존하지 않고 명시적으로 끈다.
    """
    monkeypatch.setenv("PATHFINDER_FILE_QUESTIONS", "false")

from aipds.agent.claude_driver import ClaudeDriver
from aipds.agent.pending_store import PENDING_KEY
from tests.driver_contract import assert_driver_contract
from tests.fakes.fake_sdk_asking import cancel_pending_callbacks, sdk_client_for
from tests.fakes.in_memory_s3 import FakeS3Store

# 계약이 run_answers에 넘기는 interrupt_id(driver_contract.py:128,154).
_CONTRACT_IID = "i-42"
# 재시작 경로가 되번역할 질문. S3에 심는 레코드에 함께 쓴다.
_SEEDED_SDK_QUESTIONS = [{"question": "다음 단계는?",
                          "options": [{"label": "진행"}, {"label": "종료"}]}]


@pytest.fixture(autouse=True)
def _cleanup_parked_callbacks():
    """questions 대본은 답변 없이 끝나므로 파킹된 can_use_tool 태스크가 남는다 —
    루프가 닫힐 때 "Task was destroyed but it is pending!"으로 새어나온다."""
    yield
    cancel_pending_callbacks()


def _seed_restart_pending(s3: FakeS3Store) -> None:
    """run_answers만 태우는 대본(echo_answers/followup_questions)을 위한 준비.

    계약은 make_driver로 새 드라이버를 받은 직후 run_answers를 부른다 — 인메모리
    future가 없으므로 드라이버는 "백엔드 재시작" 경로(_resume_with_answers)를
    탄다. 그 경로가 저장된 sdk_questions로 프롬프트를 만들므로 S3에 pending
    레코드가 있어야 한다. 즉 이 seed는 우회가 아니라 재시작 상태의 재현이다.

    이벤트 루프가 이미 도는 중이라(pytest-asyncio) save_pending을 await 없이
    부를 수 없으므로 FakeS3Store의 동기 텍스트 뷰에 같은 shape을 직접 쓴다.

    interrupt_id는 계약이 넘기는 값("i-42", driver_contract.py:128,154)과 같아야
    한다 — 드라이버가 이제 두 경로 모두에서 라운드를 검증하므로 다른 값을 심으면
    정당하게 거부된다. 그래서 echo의 정직성(받은 값 vs 저장된 값)은 여기서
    증명할 수 없고, 값이 서로 다른 전용 테스트가
    test_claude_driver.py::test_the_answer_record_echoes_the_received_values가
    담당한다.
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
    # place_rules가 요구하는 최소 레이아웃.
    core = Path(rules) / "aws-aiplc-rules"
    core.mkdir(parents=True)
    (core / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    # 언어 지시는 이 픽스처에 없다 — `rules_dir`가 아니라 코드
    # (agent/workspace_rules.LANGUAGE_DIRECTIVES)에서 오므로 최소 레이아웃의
    # 일부가 아니다.

    s3 = FakeS3Store()
    driver = ClaudeDriver(workspace=ws, rules_dir=rules,
                          config_dir=tempfile.mkdtemp(), s3=s3,
                          client_factory=lambda session: None)

    def factory(session):
        # 드라이버 자신의 _on_can_use_tool을 실제 팩토리가 배선하는 자리
        # (ClaudeAgentOptions(can_use_tool=...))와 같은 곳에 꽂는다.
        return sdk_client_for(scripted, driver._on_can_use_tool)

    driver._client_factory = factory  # type: ignore[assignment]

    if scripted.get("echo_answers") or scripted.get("followup_questions"):
        _seed_restart_pending(s3)

    return driver, {"session_id": "s-1"}


@pytest.mark.asyncio
async def test_claude_driver_satisfies_the_same_contract():
    await assert_driver_contract(_make_claude_driver)
