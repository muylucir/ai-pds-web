# backend/aipds/proto/build_guard.py -- the build agent's Bash decision logic.
#
# **Why this file exists (the 2026-08-01 incident).** The build agent launched Playwright
# chromium for browser verification, that verification targeted port 3000, and the AI-PDS
# frontend was killed by SIGKILL. The backend and frontend **run as the same user (`aipds`)**
# as the build agent, so there was nothing to stop the signal, and workshop participants saw
# "the connection was lost" on screen.
#
# Neither mitigation at the time was code. `skills=["shadcn-design"]` (builder.py) is, as it
# says of itself, **a context filter and not a sandbox**, and the other was prose in
# `proto-config/CLAUDE.md`. That prose forbade the thing while also teaching the way around
# it ("If you really must start a server": `setsid npm run start ...`). This module replaces
# that with code.
#
# The SDK explains why enforcement is a hook. The build runs under `bypassPermissions`
# (builder.DEFAULT_PERMISSION_MODE) and _get_can_use_tool_shadowed_warning in
# claude_agent_sdk/types.py says: "To gate every tool call, use a PreToolUse hook instead."
# Discovery already runs on the same wiring (the hooks comment in claude_driver.py -- "a
# PreToolUse hook is this product's only effective gate").
#
# **It is language-neutral.** It returns only the fragment being refused; the wording is owned
# in two versions by proto/prompts.py -- that file's header states the convention that text
# the model reads must be in the project's language, and Korean placed here would leak into an
# English project.
#
# **It is a denylist, not an allowlist.** The build agent has a wide legitimate path of
# reading files, writing files and running `npm run build`. An allowlist would block all of
# it, and a blocked build becomes pressure to turn the gate off -- the lesson discovery_guard
# recorded in the same position, that false positives shorten a gate's life.
#
# **It narrows rather than seals.** Bash is arbitrary code execution, so no denylist can block
# every way around it (launching a browser through `node -e`). What is blocked here is **the
# observed path and its immediate neighbours**. Real isolation means separating the build agent
# into its own user (the skills comment in builder.py left that as "a separate matter") and
# this gate does not substitute for it.
from __future__ import annotations

import re

#: Quoted content. Stripped before the decision -- blocking a legitimate command that states
#: a convention or writes a log, such as `echo "npm run dev는 금지"`, is a false positive
#: (the same reason, and the same risk, as discovery_guard._QUOTED).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

#: Browser automation. The direct cause of the 2026-08-01 incident, and since checking the
#: screen is what the Prototypes tab's live preview does, the build agent has no legitimate
#: use for it.
#:
#: Why `test:e2e` is on the list: the repo's `playwright.config.ts` **targets port 3000**, so
#: this one script reproduces the incident exactly.
_BROWSER = re.compile(
    r"\b(playwright|puppeteer|chrome-headless|chromium(?:-browser)?"
    r"|test:e2e)\b")

#: dev and production servers. `npm run start` is hosting's job (`start()` in
#: proto/host.py), and if the build agent launches one it holds the port after the turn ends
#: -- the command to clean that up was the kill on 2026-08-01. `npm run build` is enough for
#: build verification.
#:
#: Why `(?:run\s+)?` is optional: `pnpm dev` and `yarn start` have no `run`. The second
#: branch is the form that bypasses `npm run` and calls the framework binary directly.
_SERVER = re.compile(
    r"\b(?:npm|npx|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start)\b"
    r"|\bnext\s+(?:dev|start)\b")

#: Terminating a process it did not start.
#:
#: A bare `kill <pid>` is **not blocked**: `_SERVER` above already blocks starting a server,
#: so there is no process of its own to kill, and blocking broadly only adds false positives.
#: What is blocked is the form that finds its own target -- `pkill`/`killall` (pattern
#: matching), `fuser -k` (the port's holder), and `kill -9 $(lsof -ti:...)`, which obtains the
#: PID through command substitution (the shape at the time of the incident).
#:
#: `\bkill\b` does not match `pkill`: `p` and `k` are both word characters, so there is no
#: boundary between them.
_KILL = re.compile(
    r"\b(?:pkill|killall)\b"
    r"|\bfuser\b[^;|&]*\s-k\b"
    r"|\bkill\b[^;|&]*\$\(")

#: AI-PDS's own ports. 3000 is the frontend, 8000 the backend.
#:
#: **They do not overlap the range hosting assigns.** `_scan_port` uses `range(4000, 8000)`
#: (proto/host.py), so 3000 and 8000 are never assigned to any prototype -- blocking the two
#: does not collide with a legitimate prototype port.
#:
#: The numeric boundary is expressed with lookaround. With `\b`, `13000` and `80000` would
#: match too, and that false positive blocks a harmless command.
_PORT = re.compile(r"(?<![0-9])(?:3000|8000)(?![0-9])")

#: The decision order. Whatever matches first is what gets named -- one command can violate
#: several clauses (`fuser -k 3000/tcp` is both a termination and a port), and which one is
#: named then has to be deterministic (a test pins that order).
_PATTERNS = (_BROWSER, _SERVER, _KILL, _PORT)


def bash_denial(command: str | None) -> str | None:
    """The fragment to refuse for browser automation, starting a server, terminating another
    process, or an AI-PDS port; else None.

    Non-string input and an empty command are **allowed**. Refusing without a basis would let
    one call shape we do not know about block the whole build -- this gate's failure direction
    has to be "pass".
    """
    if not command or not isinstance(command, str):
        return None
    scrubbed = _QUOTED.sub(" ", command)
    for pattern in _PATTERNS:
        match = pattern.search(scrubbed)
        if match:
            return match.group(0).strip()
    return None
