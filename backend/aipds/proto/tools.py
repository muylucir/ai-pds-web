# backend/aipds/proto/tools.py — custom MCP tool for the prototype builder.
#
# There is only one: build_complete. File operations and questions are handled by
# the SDK's built-in tools (Write/Edit/AskUserQuestion). The reason this one is
# home-made is the same as Discovery's report_stage -- the fact that "the build is
# done" can only be trusted when the model explicitly declares it. Inferring it
# backwards from artifact existence or the done event misjudges a mid-build turn as
# completion (done only means "this turn ended").
#
# This declaration ends the session's lifetime: proto/session.py observes this event,
# sets status to "complete", writes handoff.json, and then closes the session via the
# idle timer. So the tool must not be able to declare something false, and the
# artifact validation below prevents that.
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from aipds.models import AgentEvent
from aipds.proto import prompts
from aipds.proto.design_sync import theme_imported, theme_required
from aipds.proto.session import has_build_output
from aipds.proto.store import completed_digest, mark_completed, output_digest

_log = logging.getLogger("aipds.proto")

#: A value distinct from Discovery's "aipds" -- the two drivers expose different tool
#: sets. With the same name, the logs could not tell which side's tools were
#: attached.
PROTO_MCP_SERVER_NAME = "aipds_proto"

#: The canonical name to put in allowed_tools. The SDK builds names in this form when
#: it serializes --mcp-config, so any other spelling silently stays pending approval
#: (the same point made at agent/claude_driver.py:419-422).
BUILD_COMPLETE_TOOL = f"mcp__{PROTO_MCP_SERVER_NAME}__build_complete"

# Use an explicit JSON Schema. @tool's dict shortcut ({"key": type}) makes every key
# required (create_sdk_mcp_server._build_schema), so remaining could not be omitted
# -- agent/tools.py:32-41 made the same choice for the same reason.
_BUILD_COMPLETE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "remaining": {"type": "string"},
        "changes": {"type": "string"},
    },
    "required": ["summary", "changes"],
}


def _text_result(text: str) -> dict[str, Any]:
    """The return contract of an @tool handler -- create_sdk_mcp_server.call_tool
    converts this shape into a CallToolResult."""
    return {"content": [{"type": "text", "text": text}]}


def _has_output(workspace: str) -> bool:
    """Whether anything exists under prototype/.

    The decision lives in `has_build_output` in `proto/session.py` -- the single
    definition of "built". This place, the list route, and the opening prompt ask the
    same question, and if the criteria diverge, the tool accepts completion while the
    list does not show it as built (or the reverse). This wrapper only adapts the input
    shape (a workspace string).
    """
    return has_build_output(Path(workspace))


def build_proto_tools(workspace: str,
                      emit: Callable[[AgentEvent], None],
                      language: str = "ko") -> list:
    """A list of SdkMcpTool bound to the workspace + event sink.

This list itself cannot go straight into ClaudeAgentOptions; the caller
    (proto/builder.py) wraps it with create_sdk_mcp_server(name=PROTO_MCP_SERVER_NAME,
    tools=...).

    **This is the only custom tool left in the product.** Discovery's tools all moved
    to PostToolUse hooks (agent/reconcile.py). `build_complete` remains because it
    passes the same decision criterion -- the build's last Write is indistinguishable
    from any other Write, so the "done" signal cannot be derived from files.

    language is the language of the tool description and return strings -- all three
    are prompts the model reads, so they must match the conversation language
    (proto/prompts.py).
    """

    @tool("build_complete",
          prompts.build_complete_description(language),
          _BUILD_COMPLETE_SCHEMA)
    async def build_complete(args: dict[str, Any]) -> dict[str, Any]:
        summary = args["summary"]
        remaining = args.get("remaining", "")
        changes = str(args.get("changes", "")).strip()

        # The build cannot finish with the changes-versus-spec left empty. That record
        # is the input to the next improvement session and to rebuilds, and silence is
        # indistinguishable from "not recorded" rather than "nothing changed". If
        # nothing changed, it should just say so.
        if not changes:
            _log.warning("build_complete refused: changes missing (%s)",
                         workspace)
            return _text_result(prompts.build_complete_changes_missing(language))

        # This event ends the session. If declared without artifacts, the user sees a
        # "build complete" card with nothing to host -- blocked here so the tool cannot
        # declare something false. The return string
        # tells the agent what to do so it can read it and fix things itself.
        if not _has_output(workspace):
            _log.warning("build_complete refused: prototype/ is empty (%s)",
                         workspace)
            return _text_result(prompts.build_complete_rejection(language))

        # If `prototype/` is unchanged since the last completion declaration, there is
        # nothing to finish. Every message after completion opens a new improvement
        # session, so even a single question was closed by this declaration as soon as
        # the session opened -- observed: two sessions that asked how seller information
        # is fetched both ended with `changes: 없음` without changing any code, and the
        # answer went out only as the completion card's summary, not in the chat. That
        # summary overwrote the handoff, so the next session started with "no code
        # changed" instead of the previous build. The decision is based on file
        # content: something deleted via Bash counts as changed too.
        #
        # Why this comes before the theme check: if the theme rejection went out first
        # in a session that only asked a question, theme work the user never requested
        # would start.
        build_dir = Path(workspace)
        digest = await asyncio.to_thread(output_digest, build_dir)
        if digest == completed_digest(build_dir):
            _log.warning("build_complete refused: nothing changed since the last "
                         "completion (%s)", workspace)
            return _text_result(prompts.build_complete_unchanged(language))

        # If the workspace has a brand profile applied but no theme attached, send it
        # back. The decision looks only at disk (design_sync.theme_required) -- the
        # tool-call path does not touch S3. Without a profile this check does not run
        # at all, so behavior is indistinguishable from before.
        if theme_required(build_dir) and not theme_imported(build_dir):
            _log.warning("build_complete refused: brand theme not applied (%s)",
                         workspace)
            return _text_result(prompts.build_complete_theme_rejection(language))

        # Written before the event -- the session takes a snapshot on receiving this
        # event, so the marker is included in that generation (store.COMPLETED_MARKER).
        mark_completed(build_dir, digest)
        emit(AgentEvent(kind="build_complete", payload=json.dumps(
            {"summary": summary, "remaining": remaining, "changes": changes},
            ensure_ascii=False)))
        return _text_result(prompts.build_complete_recorded(language))

    return [build_complete]
