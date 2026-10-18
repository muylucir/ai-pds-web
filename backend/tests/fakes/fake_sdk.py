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
    # For a message sent by a subagent, this carries the id of the Agent tool call that
    # launched it (observed 2026-09-15: even with default options, a subagent's tool_use
    # blocks come with this field — see the forward_subagent_text description in
    # claude_agent_sdk 0.2.143 types.py).
    # None means the message is from the orchestrating agent.
    parent_tool_use_id: str | None = None


@dataclass
class TaskStartedMessage:
    """A subagent started. `tool_use_id` is the id of the Agent tool call, and that value
    equals the subagent messages' `parent_tool_use_id` — the join key between the two streams.

    In the real SDK this is a **subclass** of SystemMessage, so `type(msg).__name__` is
    `"TaskStartedMessage"`, not `"SystemMessage"`. A translator that matches by name and
    does not know this silently drops it (which is exactly why this feature was missing)."""
    task_id: str
    description: str
    tool_use_id: str | None = None
    task_type: str | None = "local_agent"
    subtype: str = "task_started"


@dataclass
class TaskProgressMessage:
    """A subagent heartbeat. `last_tool_name` is the tool that agent just ran."""
    task_id: str
    description: str = ""
    last_tool_name: str | None = None
    tool_use_id: str | None = None
    usage: dict | None = None
    subtype: str = "task_progress"


@dataclass
class TaskNotificationMessage:
    """A subagent finished (completed, failed, or stopped)."""
    task_id: str
    status: str = "completed"
    summary: str = ""
    output_file: str = ""
    tool_use_id: str | None = None
    usage: dict | None = None
    subtype: str = "task_notification"


@dataclass
class TaskUpdatedMessage:
    """A state transition. **Sometimes termination arrives only this way** — the SDK says so:
    a background task, or a task killed by TaskStop, may report only its terminal status
    here, without a TaskNotification (TaskUpdatedMessage in types.py)."""
    task_id: str
    patch: dict
    status: str | None = None
    subtype: str = "task_updated"


@dataclass
class StreamEvent:
    """A frame emitted by partial message streaming (`include_partial_messages=True`).

    The real SDK also carries `uuid`/`session_id`, but the translator looks only at
    `event`, which contains the Claude API's raw streaming event as is
    (content_block_start / content_block_delta / content_block_stop / ...).
    """
    event: dict


@dataclass
class ResultMessage:
    subtype: str = "success"
    result: str | None = None
    # Fields through which the real SDK's ResultMessage reports failure (claude_agent_sdk/types.py).
    # The CLI does not raise turn failures as exceptions; it sends them here — Bedrock
    # 429/500/529, tool deadlocks, and interrupted streams all arrive with is_error=True.
    # The default is success, so existing scripts work unchanged, and only tests that
    # reproduce failures turn it on explicitly.
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
