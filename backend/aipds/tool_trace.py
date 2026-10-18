# backend/aipds/tool_trace.py — the "what it did" attached to the reasoning trace.
#
# On screen, Write showed as `📝 파일 변경: aiplc-docs/audit.md` (a separate `file_changed`
# event carries the `path`), but Read/Bash showed only `Read`, `Bash`. What was read and
# which command was run are the whole point of the trace, and that was missing.
#
# **Why this module is the single owner.** Two places produce the same value — live
# (`agent/claude_driver._translate`) and restore (`session_history`). If those two
# renderings diverge, the screen differs before and after a refresh. The matching branch
# in session_history already carries a comment saying "same rendering as the live status
# event"; this module turns that comment into code.
#
# **Labels are not built here.** The backend only supplies the value ("aiplc-docs/audit.md");
# the icon and separator in `🔍 Read · …` are drawn by the frontend in the UI language —
# `file_changed` already follows that discipline (the backend sends only the path, "파일 변경"
# comes from the frontend), and it is the same judgment as error_codes.py's
# "do not build a translation system in the backend".
from __future__ import annotations

#: Must fit on one line. Bash commands have no length limit (measured: hundreds of
#: characters), so without truncation the accordion becomes unreadable.
DETAIL_MAX = 120

#: Tool name → the argument key that holds that tool's "what".
#:
#: **This is an allowlist.** Printing arbitrary arguments of an unknown tool (e.g., the first
#: value) leaks internal identifiers to the screen without knowing which value is meaningful.
#:
#: Why Write/Edit/MultiEdit are absent: the `file_changed` event already carries the path,
#: so providing it here too would show the same information on two lines. `mcp__aipds__*` is
#: absent too — the dedicated stage/document/build_complete events already send structured values.
_DETAIL_KEYS: dict[str, str] = {
    "Read": "file_path",
    "Bash": "command",
    "Glob": "pattern",
    "Grep": "pattern",
    "ToolSearch": "query",
    "WebFetch": "url",
    # The tool that launches a subagent (measured on claude_agent_sdk 0.2.143: the name is
    # `Agent`, not the old `Task`). `description` is the work handed to that agent — without
    # it, the trace only records "launched an agent" and not what it was asked to do.
    # `prompt` is not used: it runs to thousands of characters and does not fit on one line.
    "Agent": "description",
}

#: Paths for file tools — used **only in subagent rows** (`file_tools=True`).
#:
#: These values are deliberately left out of the main trace: the `file_changed` event
#: already carries the path, so the same information would show on two lines (see the
#: allowlist comment above). That reasoning does not hold for rows — `file_changed` does not
#: say which agent wrote the file, so for a row to show its target, it must come from the
#: tool call.
_FILE_DETAIL_KEYS: dict[str, str] = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
}

#: Tools whose value is a path — keep only the part under the workspace.
_PATH_TOOLS = frozenset({"Read", "Write", "Edit", "MultiEdit"})

#: Top-level artifact directories of the workspace. Absolute paths are cut starting here.
#:
#: **Why the workspace path is not taken as an argument.** This function is called from both
#: live (the driver, which knows the workspace) and restore (session_history, which sees only
#: the transcript). If only one side knows the workspace, the same call yields two renderings,
#: and that is exactly the divergence this module exists to remove. Marker-based cutting
#: yields the same value on both sides.
#:
#: The list points at the same locations as runner._RESTORE_PREFIXES (that one is the list of
#: top-level prefixes for S3↔local sync).
_WORKSPACE_MARKERS = ("aiplc-docs/", "prototypes/", "prototype/", "uploads/")


def _shorten_path(value: str) -> str:
    for marker in _WORKSPACE_MARKERS:
        idx = value.find(marker)
        if idx > 0:
            return value[idx:]
    return value


def tool_detail(name: str, tool_input: object, *,
                file_tools: bool = False) -> str | None:
    """The one line to show on screen for a `name` tool call. None if there is nothing to show.

    `file_tools` also includes the paths of Write/Edit/MultiEdit — for subagent rows only
    (rationale in `_FILE_DETAIL_KEYS`). The main trace calls it with the default.

    `tool_input` is **a value produced by the model** — even if its shape is off, this does
    not raise. The trace is auxiliary information, and the turn must not die because of it.

    Redaction is the caller's responsibility: live passes it through `routes/turns._redacted`,
    restore passes it through `redact_credentials` in `session_history`. So this value must
    always ride in `text` or `payload` — `path` is treated as a structural field and does not
    go through redaction, and Bash commands are a typical place where credentials appear.
    """
    key = _DETAIL_KEYS.get(name)
    if key is None and file_tools:
        key = _FILE_DETAIL_KEYS.get(name)
    if key is None or not isinstance(tool_input, dict):
        return None
    raw = tool_input.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if name in _PATH_TOOLS:
        # Printing the absolute path as-is prepends `/opt/aipds/workspaces/{pid}/` —
        # meaningless to the user, and it leaks the project id into the trace. Paths outside
        # the workspace are left untouched (that in itself is a signal).
        value = _shorten_path(value)
    if len(value) > DETAIL_MAX:
        value = value[:DETAIL_MAX] + "…"
    return value
