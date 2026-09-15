# backend/aipds/agent_activity.py — 서브에이전트 한 명의 "지금 무엇을 하고 있는가".
#
# 에이전트가 일을 서브에이전트에 넘기기 시작하면서 빌드 화면이 몇 분간 멈춘 것처럼
# 보였다. 진행 상황이 오지 않아서가 **아니다** — SDK는 `Task*` 메시지로 에이전트별
# 하트비트를 이미 보내고 있었고(실측: claude_agent_sdk 0.2.143), 그 네 종류가
# `SystemMessage`의 서브클래스라서 `type(msg).__name__` 동등 비교를 쓰는 번역부를
# 전부 그냥 통과했다. 이 모듈은 그 하트비트를 화면이 읽는 한 가지 모양으로 굳힌다.
#
# **왜 `status`가 아니라 새 이벤트인가.** `status`는 화면에서 "가장 마지막에 일어난
# 일" 하나로 접힌다(liveActivity.ts) — 그것이 병렬 작업에서 정확히 실패하는 모양이다.
# 서브에이전트 셋이 도는 동안 그 한 줄은 셋 사이에서 깜빡이고, 어느 것도 진행으로
# 읽히지 않는다. 행이 여러 개여야 하고, 여러 개가 되려면 이벤트가 **어느** 에이전트의
# 것인지 말해야 한다. `task_id`가 그 값이고, 그것이 이 이벤트가 따로 있는 이유 전부다.
#
# **라벨은 여기서 만들지 않는다.** `tool_trace.py`와 같은 규율이다 — 백엔드는 값만
# 준다(`tool: "Write"`), `📝 파일 작성`은 프론트가 UI 언어로 그린다. 특히 중요한
# 이유가 하나 더 있다: `TaskProgressMessage.description`은 CLI가 만든 **영어** 산문
# ("Writing a.txt")이라 ko 프로젝트에서 그대로 쓸 수 없다. 그래서 그 필드는 버리고
# `last_tool_name`만 싣는다 — 프론트의 ACTIVITY_LABEL_KEYS가 이미 도구 이름을 UI
# 언어로 옮긴다.
from __future__ import annotations

import json

#: 이벤트 kind. `models.AgentEvent.kind`의 Literal과 같은 값이어야 한다.
AGENT_ACTIVITY = "agent_activity"

#: 요약 한 줄의 상한. 서브에이전트 요약은 실측 수백 자다("Done.\n\n- Created
#: `/…/a.txt` containing exactly `alpha`…"). 트레이스는 한 줄 목록이므로 자르지
#: 않으면 읽히지 않는다 — tool_trace.DETAIL_MAX와 같은 판단이고, 값이 다른 것은
#: 요약이 문장이고 detail은 경로·명령이기 때문이다.
SUMMARY_MAX = 160

#: 태스크가 끝났다는 뜻의 status 값. **두 어휘를 함께 덮는다** —
#: `task_notification`은 `stopped`를, `task_updated`는 날것의 `killed`를 보낸다.
#:
#: SDK의 `TERMINAL_TASK_STATUSES`와 같은 값이고, 그 동등성은
#: tests/test_agent_activity.py가 지킨다. 상류를 런타임에 import하지 않는 이유는
#: 번역부가 SDK 타입에 의존하지 않기 때문이다(그래서 tests/fakes가 동작한다).
TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "stopped", "killed"}
)


def activity_payload(*, task_id: str, state: str, label: str | None = None,
                     tool: str | None = None, detail: str | None = None,
                     status: str | None = None,
                     summary: str | None = None) -> str:
    """한 에이전트 행의 갱신 한 건 → SSE payload(JSON 문자열).

    `task_id`가 행의 키이고 `state`가 그 행에 무슨 일이 일어났는지다:

      started   그 에이전트가 떴다. `label`이 맡은 일이다(TaskStarted.description).
      progress  `tool`을 돌리고 있다. `detail`은 그 대상(파일 경로 등).
      done      끝났다. `status`가 어떻게(completed/failed/stopped/killed),
                `summary`가 그 에이전트가 남긴 한 줄.

    **None인 필드는 키째로 빠진다.** 부재와 null은 프론트에서 다르게 읽힌다:
    병합 규칙이 `detail`의 부재를 "이전 값을 유지"로 다루므로(같은 도구가 계속
    도는 동안 하트비트가 대상을 지우지 않게 하는 장치), null을 보내면 그 규칙이
    매 프레임 행의 대상을 지운다.
    """
    payload: dict[str, str] = {"task_id": task_id, "state": state}
    if label is not None:
        payload["label"] = label
    if tool is not None:
        payload["tool"] = tool
    if detail is not None:
        payload["detail"] = detail
    if status is not None:
        payload["status"] = status
    if summary is not None:
        text = summary.strip()
        if text:
            payload["summary"] = (text[:SUMMARY_MAX] + "…"
                                  if len(text) > SUMMARY_MAX else text)
    return json.dumps(payload, ensure_ascii=False)
