# backend/aipds/agent/discovery_guard.py -- deciding Discovery's write scope.
#
# **Why this file exists (the 2026-08-16 defect).** The Discovery agent went ahead
# and built a prototype in the workspace as `prototype/index.html`. Building and
# hosting are the Prototypes tab's job and Discovery ends at writing the spec (the
# "Prototypes" section of discovery-config/CLAUDE.md), but that rule was **prose
# only** -- and what the prose forbade were build *commands*: npm install, npm run
# dev, starting a subprocess, choosing a port. A single self-contained HTML file needs
# none of those. The agent's own report ("no API key, no package installation and no
# external communication are required") is the evidence that it satisfied every
# enumerated clause. **An enumeration invites the item it left out.**
#
# The SDK explains why there was no means of enforcement. Discovery runs under
# `bypassPermissions` (claude_driver.DEFAULT_PERMISSION_MODE), and
# _get_can_use_tool_shadowed_warning in claude_agent_sdk/types.py says:
#
#   "can_use_tool will not be invoked: permission_mode 'bypassPermissions'
#    auto-approves every tool call ... To gate every tool call, use a
#    PreToolUse hook instead."
#
# This module is that hook's decision logic. The wiring is in
# claude_driver._on_pre_tool_use.
#
# **It is language-neutral.** It returns only the thing being refused (a path or a
# command fragment); the wording is owned in two versions by agent/prompts.py -- that
# file's header states the convention that text the model reads must be in the
# project's language, and Korean placed here would leak into an English project.
#
# **Bash is narrowed, not sealed.** Unlike the path-based Write/Edit decision, Bash is
# arbitrary code execution, so no denylist can block every way around it (opening a
# file through `python3 -c`, for instance). What is blocked here is the observed path
# and its immediate neighbours. Sealing it completely would mean removing Bash from
# Discovery altogether (Read/Glob/Grep are enough for exploring the rules), and that is
# a separate decision not taken here.
from __future__ import annotations

import re
from pathlib import PurePosixPath

from aipds.pathsafe import workspace_relative

#: The only root Discovery may write to. It is the definition of an artifact
#: (Workspace.list_artifacts treats the same subtree as the project's output).
DOCS_ROOT = "aiplc-docs"

#: The file-writing tools this gate sees. Must be the same set as
#: claude_driver._FILE_TOOLS: a tool added to only one of them ends up observed but
#: not blocked.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def write_denial(path: str | None, workspace: str) -> str | None:
    """The path to refuse when it falls outside `aiplc-docs/`, else None.

    A call whose path is unknown (no file_path) is **allowed**. Refusing without a
    basis would let one tool shape we do not know about block the whole turn -- this
    gate's failure direction has to be "pass", and observation is already handled by
    PostToolUse.
    """
    if not path or not isinstance(path, str):
        return None
    rel = workspace_relative(path, workspace)
    if rel is None:
        # An escape from the workspace. It cannot be relativised, so the original
        # is named verbatim.
        return path
    # This has to compare segments rather than string **prefixes**:
    # "aiplc-docs-backup/x.md" passes startswith("aiplc-docs").
    if PurePosixPath(rel).parts[:1] == (DOCS_ROOT,):
        return None
    return rel


#: Quoted content. Stripped before redirection and command detection -- reading the
#: `>` in `echo "a > b"` as a redirection would block a legitimate command that writes
#: a document, and that creates pressure to turn the gate off (false positives shorten
#: a gate's life).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

#: Package managers. The invocation itself is blocked rather than enumerating
#: subcommands -- Discovery writes markdown and has no work that needs a package
#: manager, and the lesson of this defect is that an enumeration invites the item it
#: left out.
_PKG = re.compile(r"(?:^|[;&|(]\s*|\s)(npm|npx|pnpm|yarn|bun)\b")

#: Dev servers and runtime serving. `npx serve` and friends are already caught by
#: _PKG above, so a bare `serve` is not listed here -- listing it would catch every
#: filename and body containing "serve".
_SERVERS = re.compile(
    r"\b(http\.server|SimpleHTTPServer|uvicorn|gunicorn|flask\s+run"
    r"|php\s+-S|vite|next\s+(?:dev|start))\b")

#: Redirection into a file. `(?<![0-9&])` excludes the fd forms (`2>`, `>&`):
#: `2>/dev/null` and `2>&1` are idioms, not file creation.
_REDIR = re.compile(r"(?<![0-9&])>{1,2}\s*(?!&)([^\s;|&<>]+)")

#: tee creates files too. Blocking only redirection leaves this way straight open.
_TEE = re.compile(r"\btee\b\s+(?:-a\s+)?([^\s;|&<>]+)")

#: Targets that are devices rather than files. Excluded from the redirection check.
_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr")


def bash_denial(command: str | None) -> str | None:
    """The fragment to refuse for a build, a server start, or file creation outside
    `aiplc-docs/`, else None.

    It is a **denylist**, not an allowlist. Discovery has a legitimate path of exploring
    the rule files with `ls`/`grep`/`find`, and an allowlist would block all of it.
    """
    if not command or not isinstance(command, str):
        return None
    # The decision is made on a copy with quoted content stripped. The original is
    # not reported -- what gets named is the fragment matched below.
    scrubbed = _QUOTED.sub(" ", command)

    pkg = _PKG.search(scrubbed)
    if pkg:
        return pkg.group(1)

    server = _SERVERS.search(scrubbed)
    if server:
        return server.group(1)

    for pattern in (_REDIR, _TEE):
        for match in pattern.finditer(scrubbed):
            target = match.group(1)
            if target in _DEVICES:
                continue
            # A redirection target arrives as a workspace-relative path (the cwd is
            # the workspace).
            if PurePosixPath(target).parts[:1] == (DOCS_ROOT,):
                continue
            return target
    return None
