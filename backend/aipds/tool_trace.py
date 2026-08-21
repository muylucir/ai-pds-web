# backend/aipds/tool_trace.py -- the "what it did" attached to a reasoning trace.
#
# On screen a Write appears as `📝 파일 변경: aiplc-docs/audit.md` (a separate
# `file_changed` event carries the `path`), while Read and Bash showed only `Read` and
# `Bash`. What was read and what command was run is the whole point of the trace, and it
# was missing.
#
# **Why this module is the single owner.** Two places produce the same value -- live
# (`agent/claude_driver._translate`) and restore (`session_history`). If those two
# representations diverge, the screen differs across a refresh. The relevant branch in
# session_history already carried a comment saying "the same representation as the live
# status event", and this module turns that comment into code.
#
# **Labels are not made here.** The backend supplies only the value
# ("aiplc-docs/audit.md"); the icon and separator in `🔍 Read · …` are drawn by the
# frontend in the UI language -- `file_changed` already follows that discipline (the
# backend sends only the path, the frontend supplies "파일 변경"), and it is the same
# judgement as error_codes.py's "we do not build a translation system in the backend".
from __future__ import annotations

#: It has to fit on one line. A Bash command has no length limit (measured: hundreds of
#: characters), and without truncation the accordion becomes unreadable.
DETAIL_MAX = 120

#: Tool name -> the argument key holding that tool's "what".
#:
#: **It is an allowlist.** Printing an unknown tool's arguments arbitrarily (the first
#: value, say) leaks internal identifiers onto the screen without knowing which value is
#: the meaningful one.
#:
#: Why Write/Edit/MultiEdit are absent: the `file_changed` event already carries the path,
#: so supplying it here too would show the same information on two lines.
#: `mcp__aipds__*` is absent as well -- the dedicated stage/document/build_complete events
#: already send structured values.
_DETAIL_KEYS: dict[str, str] = {
    "Read": "file_path",
    "Bash": "command",
    "Glob": "pattern",
    "Grep": "pattern",
    "ToolSearch": "query",
    "WebFetch": "url",
}

#: Tools whose value is a path -- only the part below the workspace is kept.
_PATH_TOOLS = frozenset({"Read"})

#: The workspace's top-level artifact directories. An absolute path is cut from here.
#:
#: **Why the workspace path is not taken as an argument.** This function is called both
#: live (from the driver, which knows the workspace) and on restore (from session_history,
#: which sees only the transcript). If only one side knew the workspace, the same call
#: would produce two representations -- exactly the divergence this module exists to
#: remove. A marker-based cut yields the same value on both sides.
#:
#: The list points at the same places as runner._RESTORE_PREFIXES (that one is the list of
#: top-level prefixes for S3<->local synchronisation).
_WORKSPACE_MARKERS = ("aiplc-docs/", "prototypes/", "prototype/", "uploads/")


def _shorten_path(value: str) -> str:
    for marker in _WORKSPACE_MARKERS:
        idx = value.find(marker)
        if idx > 0:
            return value[idx:]
    return value


def tool_detail(name: str, tool_input: object) -> str | None:
    """The one line to show on screen for a `name` tool call. None when there is nothing to
    show.

    `tool_input` is **a model-authored value** -- a mismatched shape does not raise. The
    trace is incidental information and must not kill a turn.

    Redaction is the caller's responsibility: live, `routes/turns._redacted` passes it
    through `redact_credentials`; on restore, `session_history` does. That is why this
    value must be carried in `text` or `payload` -- `path` is treated as a structural field
    and does not pass through redaction, and a Bash command is a classic place for
    credentials to appear.
    """
    key = _DETAIL_KEYS.get(name)
    if key is None or not isinstance(tool_input, dict):
        return None
    raw = tool_input.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if name in _PATH_TOOLS:
        # Printing an absolute path verbatim carries `/opt/aipds/workspaces/{pid}/` with
        # it -- meaningless to the user, and it leaks the project id into the trace. A path
        # outside the workspace is left alone (that is itself a signal).
        value = _shorten_path(value)
    if len(value) > DETAIL_MAX:
        value = value[:DETAIL_MAX] + "…"
    return value
