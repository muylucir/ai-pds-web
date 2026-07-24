"""Shape-compatible stand-ins for claude_agent_sdk message types + a scripted
client. sdk_driver matches on class NAME (type(msg).__name__), not isinstance,
precisely so these fakes work without importing the real SDK."""
from dataclasses import dataclass, field

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

class FakeSdkClient:
    """Scripted ClaudeSDKClient: yields `script` from receive_response()."""
    def __init__(self, script=None):
        self.script = script or []
        self.queries: list[str] = []
        self.interrupt_calls = 0
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def query(self, text):
        self.queries.append(text)

    async def receive_response(self):
        for msg in self.script:
            yield msg

    async def interrupt(self):
        self.interrupt_calls += 1
