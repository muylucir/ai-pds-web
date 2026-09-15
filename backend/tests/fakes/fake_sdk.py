# backend/tests/fakes/fake_sdk.py
"""Shape-compatible stand-ins for claude_agent_sdk message types + a scripted
client. builder.py matches on class NAME (type(msg).__name__), not isinstance,
precisely so these fakes work without importing the real SDK.

Ported from harness/tests/fake_sdk.py; `disconnect_calls` is new (the
in-process builder must be explicitly disconnected on idle/close, which the
VM era handled by stopping the whole VM)."""
from dataclasses import dataclass


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class AssistantMessage:
    content: list
    # 서브에이전트가 보낸 메시지면 그것을 띄운 Agent 도구 호출의 id가 들어온다
    # (실측 2026-09-15: 기본 옵션에서도 서브에이전트의 tool_use 블록은 이 필드를
    # 달고 온다 — claude_agent_sdk 0.2.143 types.py의 forward_subagent_text 설명).
    # None이면 총괄 에이전트의 메시지다.
    parent_tool_use_id: str | None = None


@dataclass
class TaskStartedMessage:
    """서브에이전트가 떴다. `tool_use_id`가 Agent 도구 호출의 id이고, 그 값이
    서브에이전트 메시지의 `parent_tool_use_id`와 같다 — 두 스트림의 조인 키다.

    실제 SDK에서는 SystemMessage의 **서브클래스**라서 `type(msg).__name__`이
    `"SystemMessage"`가 아니라 `"TaskStartedMessage"`다. 이름으로 매칭하는
    번역부가 이것을 모르면 조용히 버려진다(그것이 바로 이 기능이 없던 이유)."""
    task_id: str
    description: str
    tool_use_id: str | None = None
    task_type: str | None = "local_agent"
    subtype: str = "task_started"


@dataclass
class TaskProgressMessage:
    """서브에이전트의 하트비트. `last_tool_name`이 그 에이전트가 방금 돌린 도구다."""
    task_id: str
    description: str = ""
    last_tool_name: str | None = None
    tool_use_id: str | None = None
    usage: dict | None = None
    subtype: str = "task_progress"


@dataclass
class TaskNotificationMessage:
    """서브에이전트가 끝났다(완료·실패·중단)."""
    task_id: str
    status: str = "completed"
    summary: str = ""
    output_file: str = ""
    tool_use_id: str | None = None
    usage: dict | None = None
    subtype: str = "task_notification"


@dataclass
class TaskUpdatedMessage:
    """상태 전이. **종료가 이쪽으로만 오는 경우가 있다** — SDK가 명시한다:
    백그라운드 태스크나 TaskStop으로 죽은 태스크는 TaskNotification 없이
    여기서 terminal status만 보고할 수 있다(types.py의 TaskUpdatedMessage)."""
    task_id: str
    patch: dict
    status: str | None = None
    subtype: str = "task_updated"


@dataclass
class StreamEvent:
    """부분 메시지 스트리밍(`include_partial_messages=True`)이 내는 프레임.

    실제 SDK는 `uuid`/`session_id`도 싣지만 번역이 보는 것은 `event` 하나이고,
    그 안에는 Claude API의 원본 스트리밍 이벤트가 그대로 들어 있다
    (content_block_start / content_block_delta / content_block_stop / ...).
    """
    event: dict


@dataclass
class ResultMessage:
    subtype: str = "success"
    result: str | None = None
    # 실제 SDK의 ResultMessage가 실패를 알리는 필드들(claude_agent_sdk/types.py).
    # CLI는 턴 실패를 예외로 던지지 않고 여기에 담아 보낸다 — Bedrock 429/500/529,
    # 도구 교착, 중단된 스트림 모두 is_error=True로 온다. 기본값이 성공이므로
    # 기존 대본은 그대로 동작하고, 실패를 재현하는 테스트만 명시적으로 켠다.
    is_error: bool = False
    api_error_status: int | None = None
    terminal_reason: str | None = None
    errors: list | None = None


class FakeSdkClient:
    """Scripted ClaudeSDKClient: yields `script` from receive_response()."""

    def __init__(self, script=None):
        self.script = script or []
        self.queries: list[str] = []
        self.interrupt_calls = 0
        self.disconnect_calls = 0
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    async def query(self, text):
        self.queries.append(text)

    async def receive_response(self):
        for msg in self.script:
            yield msg

    async def interrupt(self):
        self.interrupt_calls += 1
