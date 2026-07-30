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
