# backend/tests/test_agent_activity.py — agent_activity payload의 계약.
#
# 이 payload는 **UI 계약**이다(백엔드가 만드는 유일한 서브에이전트 표현). 필드가
# 조용히 사라지면 화면의 행이 라벨 없이 뜨거나 영원히 닫히지 않으므로, 여기서
# 모양을 못박는다.
from __future__ import annotations

import json

from aipds.agent_activity import (SUMMARY_MAX, TERMINAL_TASK_STATUSES,
                                  activity_payload)


def test_started_carries_the_row_label():
    payload = json.loads(activity_payload(
        task_id="a040", state="started", label="화면 골격 만들기"))
    assert payload == {"task_id": "a040", "state": "started",
                       "label": "화면 골격 만들기"}


def test_none_fields_are_dropped_rather_than_sent_as_null():
    """빠진 값과 "값이 null이다"는 프론트에서 다르게 다뤄진다: 병합 규칙이
    `detail`의 **부재**를 "이전 값을 유지해라"로 읽는다(useProtoAgents). null을
    보내면 그 규칙이 매 하트비트마다 대상을 지운다."""
    payload = json.loads(activity_payload(
        task_id="a040", state="progress", tool="Write"))
    assert payload == {"task_id": "a040", "state": "progress", "tool": "Write"}
    assert "detail" not in payload
    assert "label" not in payload


def test_a_long_summary_is_truncated():
    """서브에이전트 요약은 실측 수백 자다(프로브: "Done.\\n\\n- Created …").
    트레이스는 한 줄 목록이므로 자르지 않으면 읽히지 않는다 — tool_trace의
    DETAIL_MAX와 같은 판단이다."""
    payload = json.loads(activity_payload(
        task_id="a040", state="done", status="completed", summary="가" * 500))
    assert len(payload["summary"]) == SUMMARY_MAX + 1  # 말줄임표 한 글자
    assert payload["summary"].endswith("…")


def test_terminal_statuses_match_the_sdk():
    """우리가 든 목록이 SDK의 것과 갈라지면 행이 영원히 도는 채로 남는다
    (`killed`를 빼먹는 것이 대표적 — task_updated만 그 값을 보낸다).

    SDK를 런타임에 import하지 않고 우리 상수를 쓰는 이유는 번역부가 SDK 타입에
    의존하지 않기 때문이다(fakes가 그래서 동작한다). 대신 그 값이 상류와 같은지를
    이 테스트가 지킨다."""
    from claude_agent_sdk import TERMINAL_TASK_STATUSES as upstream
    assert TERMINAL_TASK_STATUSES == upstream
