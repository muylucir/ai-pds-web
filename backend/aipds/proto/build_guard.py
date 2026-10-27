# backend/aipds/proto/build_guard.py — the build agent's Bash decision logic.
#
# **Why this file exists (the 2026-08-01 incident).** The build agent launched Playwright
# chromium for browser verification, that verification targeted port 3000, and the AI-PDS frontend
# was killed with SIGKILL. The backend and frontend **run as the same user (`aipds`)** as the build
# agent, so nothing stopped the signal, and workshop participants' screens showed "연결이 끊겼다" (disconnected).
#
# Neither of the mitigations at the time was code. `skills=["shadcn-design"]` (builder.py) is,
# as it notes itself, **a context filter, not a sandbox**, and the other was prose in
# `proto-config/CLAUDE.md`. That prose forbade it while also teaching the workaround recipe
# ("If you really must start a server": `setsid npm run start ...`). This
# module replaces that with code.
#
# The SDK itself explains why the enforcement mechanism is a hook. Builds run with
# `bypassPermissions` (builder.DEFAULT_PERMISSION_MODE), and _get_can_use_tool_shadowed_warning in
# claude_agent_sdk/types.py says: "To gate every tool call, use a
# PreToolUse hook instead." Discovery already runs with the same wiring
# (the hooks comment in claude_driver.py — "PreToolUse is this product's only effective gate").
#
# **It is language-neutral.** It returns only the rejected fragment, and proto/prompts.py owns the
# wording in two versions — per that file header's convention that text the model reads must be in the
# project language; putting Korean here would leak it into English projects.
#
# **It is a denylist, not an allowlist.** The build agent has a wide normal path of reading and writing
# files and running `npm run build`. An allowlist would block all of that, and blocked builds become
# pressure to turn the gate off — false positives shorten a gate's lifespan, the lesson
# discovery_guard records in the same place.
#
# **It narrows, it does not seal.** Bash is arbitrary code execution, so a denylist cannot block every
# workaround (launching a browser with `node -e`). What this blocks is **the observed paths and their
# close neighbors**. Real isolation means running the build agent as a separate user
# (the skills comment in builder.py leaves that as "a separate matter"), and this gate does not
# replace it.
from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import PurePosixPath
from typing import Collection
from urllib.parse import urlsplit

from aipds.agent_home import SETTINGS_IN_COMMAND

#: Content inside quotes. Stripped before the decision — blocking normal commands that write down the
#: convention or leave a log, like `echo "npm run dev는 금지"`, is a false positive (same reason as
#: discovery_guard._QUOTED, and the same risk).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

#: Browser automation. The direct cause of the 2026-08-01 incident, and checking the screen is the job of the
#: prototype tab's live preview, so the build agent has no legitimate use for it.
#:
#: Why `test:e2e` is on the list: the repo's `playwright.config.ts` **targets port 3000**, so
#: this one script reproduces the incident exactly.
_BROWSER = re.compile(
    r"\b(playwright|puppeteer|chrome-headless|chromium(?:-browser)?"
    r"|test:e2e)\b")

#: dev and production servers. `npm run start` is hosting's job (`start()` in
#: proto/host.py), and if the build agent launches it, it keeps holding the port after the turn ends — the
#: cleanup command for that was the kill of 2026-08-01. `npm run build` is enough for build verification.
#:
#: Why `(?:run\s+)?` is optional: `pnpm dev` and `yarn start` have no `run`. The second
#: branch is the form that bypasses `npm run` and calls the framework binary directly.
_SERVER = re.compile(
    r"\b(?:npm|npx|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start)\b"
    r"|\bnext\s+(?:dev|start)\b")

#: Killing processes I did not start.
#:
#: A bare `kill <pid>` is **not blocked**: `_SERVER` above already blocks starting servers, so there
#: is no process of its own to kill, and blocking broadly only adds false positives. What is blocked
#: are the forms that find their target themselves — `pkill`/`killall` (pattern matching), `fuser -k`
#: (port holders), and `kill -9 $(lsof -ti:...)`, which gets the PID through command substitution (the shape at the time of the incident).
#:
#: `\bkill\b` does not catch `pkill`: `p` and `k` are both word characters, so there is no
#: boundary between them.
_KILL = re.compile(
    r"\b(?:pkill|killall)\b"
    r"|\bfuser\b[^;|&]*\s-k\b"
    r"|\bkill\b[^;|&]*\$\(")

#: AI-PDS's own ports. 3000 is the frontend, 8000 is the backend.
#:
#: **They do not overlap the range hosting assigns.** `_scan_port` uses
#: `range(4000, 8000)` (proto/host.py), so 3000 and 8000 are never assigned
#: to any prototype — blocking the two ports does not collide with normal prototype ports.
#:
#: Numeric boundaries are matched with lookarounds. With `\b`, `13000` and `80000` would match too, and
#: those false positives would block harmless commands.
_PORT = re.compile(r"(?<![0-9])(?:3000|8000)(?![0-9])")

#: CLI settings files (agent_home.SETTINGS_IN_COMMAND). The build agent has no reason to touch them.
#: Reads (`cat`) are caught too, but those have no legitimate use either.
_SETTINGS = SETTINGS_IN_COMMAND

#: Decision order. The first match is named — one command can violate several clauses
#: (`fuser -k 3000/tcp` is both a kill and a port), and which one gets named must then
#: be deterministic (the tests pin that order).
_PATTERNS = (_BROWSER, _SERVER, _KILL, _PORT, _SETTINGS)


def bash_denial(command: str | None) -> str | None:
    """Returns the rejected fragment for browser automation, server startup, killing other processes, or
    AI-PDS ports; otherwise None.

    Non-string input and empty commands are **allowed**. Rejecting without a basis for judgment
    would let a single call shape we do not know about block the whole build — this gate's failure
    direction must be "pass".
    """
    if not command or not isinstance(command, str):
        return None
    scrubbed = _QUOTED.sub(" ", command)
    for pattern in _PATTERNS:
        match = pattern.search(scrubbed)
        if match:
            return match.group(0).strip()
    return None


#: Write-family tools. The build agent writes anywhere in the working directory — only the CLI settings are blocked.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

#: File names the CLI reads as settings. `settings*.json` under `.claude/` is project settings,
#: and `settings*.json` outside the working directory is the config dir (user settings).
_SETTINGS_NAMES = frozenset({".mcp.json", ".claude.json"})


def settings_write_denial(path: str | None, workspace: str) -> str | None:
    """The path if it is a CLI settings file, otherwise None. Passes if the path cannot be determined."""
    if not path or not isinstance(path, str):
        return None
    p = PurePosixPath(path)
    if p.name in _SETTINGS_NAMES:
        return path
    if p.name.startswith("settings") and p.suffix == ".json":
        inside = not p.is_absolute() or p.is_relative_to(workspace)
        if p.parent.name == ".claude" or not inside:
            return path
    return None


#: Hosts (and their subdomains) that WebFetch may open. These are the docs the build skills point to
#: (proto-config/skills/shadcn-design: ui.shadcn.com for components and blocks, ai-sdk.dev for AI chat
#: elements; proto-config/skills/antd-design: ant.design for component markdown, x.ant.design for AI
#: chat components). A WebFetch going anywhere else is a path for shipping workspace content out in the URL,
#: and all the build needs is the docs above and npm (Bash).
FETCH_HOSTS = ("ui.shadcn.com", "ai-sdk.dev", "ant.design")

#: The shape read as a URL from what the user wrote. A scheme is required — catching scheme-less fragments
#: like `store.example.com/x` would turn file paths and version strings into hosts. A trailing closing
#: parenthesis or quote is part of the sentence, not the URL (`(https://a.com)`).
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`)\]}]+", re.IGNORECASE)

#: Suffixes that point inside by name alone. Even a URL the user wrote must not become a path into this
#: server (the backend, other projects' preview ports).
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal")


def _internal_host(host: str) -> bool:
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # `127.1`, `2130706433`, and `0x7f.1` are IPv4 too — the URL parser on the WebFetch side reads them
        # as 127.0.0.1, while `ip_address` treats them as names. `inet_aton` reads the same shorthand.
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False  # it is a name — resolution results are not checked (that range is reachable via Bash anyway)
    return not ip.is_global


def user_url_hosts(text: str | None) -> set[str]:
    """Hosts of http(s) URLs in what the user **wrote directly**. Internal addresses are excluded.

    This is the only basis for widening WebFetch: the user asked to look at this site. So the input must
    be the user's original text — the server-wrapped opening prompt contains the model-written handoff
    summary and spec paths, and extracting from there would make URLs the model wrote down an allowance for the next session.
    """
    hosts: set[str] = set()
    for match in _URL_IN_TEXT.finditer(text or ""):
        try:
            host = (urlsplit(match.group(0)).hostname or "").lower().rstrip(".")
        except ValueError:
            continue
        if host and not _internal_host(host):
            hosts.add(host)
    return hosts


def fetch_denial(url: str | None,
                 user_hosts: Collection[str] = ()) -> str | None:
    """The host (or URL) if outside the allowed hosts, otherwise None.

    Allowed are the docs hosts (`FETCH_HOSTS`, including subdomains) and `user_hosts` — the hosts of
    URLs the user wrote in this session (`user_url_hosts`). User hosts are **exactly that host
    only**: opening subdomains too would turn a single `https://co.kr` line into all of `*.co.kr`.

    A missing or non-string URL is **rejected** — this tool's input is a single URL, and letting an
    unknown shape through would be a bypass (why the failure direction differs from the Bash check).
    """
    if not url or not isinstance(url, str):
        return str(url)
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return url
    if any(host == h or host.endswith("." + h) for h in FETCH_HOSTS):
        return None
    if host in user_hosts:
        return None
    return host
