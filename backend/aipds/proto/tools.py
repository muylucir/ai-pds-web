# backend/aipds/proto/tools.py -- the prototype builder's custom MCP tool.
#
# There is exactly one: build_complete. File manipulation and questions are handled by the
# SDK's built-in tools (Write/Edit/AskUserQuestion). The reason this one is hand-written is
# the same as Discovery's report_stage -- the fact that "the build is finished" can only be
# trusted from the model's explicit declaration. Inferring it backwards from the presence of
# output or from a done event misjudges a mid-build turn as complete (done means only "this
# turn has ended").
#
# This declaration ends the session's life: proto/session.py observes the event, moves status
# to "complete", writes handoff.json and closes the session on the idle timer. So the tool
# must not be able to declare something false, and the output check below prevents it.
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from aipds.models import AgentEvent
from aipds.proto import prompts
from aipds.proto.design_sync import theme_imported, theme_required
from aipds.proto.session import has_build_output

_log = logging.getLogger("aipds.proto")

#: A value distinct from Discovery's "aipds" -- the two drivers expose different tool sets.
#: With the same name, which side's tool was attached would be indistinguishable in the
#: log.
PROTO_MCP_SERVER_NAME = "aipds_proto"

#: The canonical name to put in allowed_tools. The SDK builds the name in this form when it
#: serialises --mcp-config, so any other spelling quietly stays pending approval (the same
#: point at agent/claude_driver.py:419-422).
BUILD_COMPLETE_TOOL = f"mcp__{PROTO_MCP_SERVER_NAME}__build_complete"

# An explicit JSON Schema is used. @tool's dict shortcut ({"key": type}) makes every key
# required (create_sdk_mcp_server._build_schema), which would make remaining impossible to
# omit -- agent/tools.py:32-41 made the same choice for the same reason.
_BUILD_COMPLETE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "remaining": {"type": "string"},
    },
    "required": ["summary"],
}


def _text_result(text: str) -> dict[str, Any]:
    """The return contract of an @tool handler -- create_sdk_mcp_server.call_tool converts
    this shape into a CallToolResult."""
    return {"content": [{"type": "text", "text": text}]}


def _has_output(workspace: str) -> bool:
    """Whether there is anything at all under prototype/.

    The decision lives in `has_build_output` in `proto/session.py` -- the single definition of
    "it has been built". This place, the list route and the opening prompt all ask the same
    question, and if the criteria diverge you get a state where the tool accepts completion
    but the list does not show built (or the other way round). This wrapper only adapts the
    input shape (a workspace string).
    """
    return has_build_output(Path(workspace))


def build_proto_tools(workspace: str,
                      emit: Callable[[AgentEvent], None],
                      language: str = "ko") -> list:
    """The list of SdkMcpTools, bound to a workspace and an event sink.

The list itself cannot go straight into ClaudeAgentOptions; the caller
    (proto/builder.py) wraps it with create_sdk_mcp_server(name=PROTO_MCP_SERVER_NAME,
    tools=...).

    **It is the only custom tool left in this product.** Discovery's have all moved to
    PostToolUse hooks (agent/reconcile.py). `build_complete` remains because it passes the
    same test -- a build's last Write is indistinguishable from any other Write, so the "it
    is finished" signal cannot be derived from a file.

    language is the language of the tool description and the return strings -- all of which
    are prompts the model reads, so they have to match the conversation's language
    (proto/prompts.py).
    """

    @tool("build_complete",
          prompts.build_complete_description(language),
          _BUILD_COMPLETE_SCHEMA)
    async def build_complete(args: dict[str, Any]) -> dict[str, Any]:
        summary = args["summary"]
        remaining = args.get("remaining", "")

        # This event ends the session. Declared with no output, the user sees a "build
        # complete" card while there is nothing to host -- blocked here so the tool cannot
        # declare something false. The return string tells the agent what to do so it can
        # read it and fix things itself.
        if not _has_output(workspace):
            _log.warning("build_complete refused: prototype/ is empty (%s)",
                         workspace)
            return _text_result(prompts.build_complete_rejection(language))

        # If this is a workspace with a brand profile applied but no theme attached, it is
        # sent back. The decision looks only at disk (design_sync.theme_required) -- no S3
        # on a tool-call path. With no profile this check does not run at all, making it
        # indistinguishable from the previous behaviour.
        build_dir = Path(workspace)
        if theme_required(build_dir) and not theme_imported(build_dir):
            _log.warning("build_complete refused: brand theme not applied (%s)",
                         workspace)
            return _text_result(prompts.build_complete_theme_rejection(language))

        emit(AgentEvent(kind="build_complete", payload=json.dumps(
            {"summary": summary, "remaining": remaining}, ensure_ascii=False)))
        return _text_result(prompts.build_complete_recorded(language))

    return [build_complete]
