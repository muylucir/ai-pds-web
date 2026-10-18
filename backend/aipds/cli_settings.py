# backend/aipds/cli_settings.py — shared settings passed to the bundled CLI (context and turn lifetime).
#
# Two agents (the Discovery driver and the prototype builder) launch the same CLI as a subprocess,
# so both use the same values. That is why the values are built in this one place —
# if only one side were switched on, the same project would get early compaction in Discovery and late
# compaction in builds, or vice versa, an inexplicable asymmetry.
#
# **Why this file exists (observed 2026-08-13).** While chasing why later documents in Korean projects
# were thinner than in English ones, we ran into compaction. A real build session (claude-opus-4-8)
# had its context summarized from **264,040 → 53,375 tokens** — it is cut at 260K, not near 1M.
# Documents written after that come from the summary rather than the evidence, so they get thinner
# in later stages. Korean uses 1.66x the tokens for the same content, so it reaches that point
# 40% earlier.
#
# The dominant cause was not this but the absence of a length standard (the depth standard in
# discovery-config/CLAUDE.md and the length clause in agent/workspace_rules.LANGUAGE_DIRECTIVES); this
# file deals with the **amplifier**. Both must be fixed together for later documents to recover.
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

#: Interpretation of boolean env values. Same discipline as _TRUTHY in routes/proto_public.py.
_TRUTHY = {"1", "true", "yes", "on"}

#: Whether to enable 1M context. Off by default — enabling it is not free (docstring below).
LONG_CONTEXT_ENV = "AIPDS_LONG_CONTEXT"

#: The context size (tokens) at which auto-compaction triggers. If unset, the CLI default.
AUTO_COMPACT_WINDOW_ENV = "AIPDS_AUTO_COMPACT_WINDOW"

#: The range the CLI accepts. Taken from the bundled CLI's (2.1.231) settings schema
#: (`autoCompactWindow: int().min(1e5).max(1e6)`). Passing a value outside it makes the CLI
#: reject the settings, and that rejection does not show up in our logs, so we block it here.
_WINDOW_MIN = 100_000
_WINDOW_MAX = 1_000_000

#: The suffix the bundled CLI accepts as the 1M context alias. The `opus[1m]`/`sonnet[1m]` forms are
#: in the CLI's official alias list and internally turn on the `context-1m-2025-08-07` beta.
_LONG_CONTEXT_SUFFIX = "[1m]"


def long_context_enabled() -> bool:
    """Whether to enable 1M context. **Off by default.**

    It is off by default because enabling it is not a strict upgrade:

    - **Per-turn cost** goes up. When compaction is delayed, the full history is resent every turn.
      Even with cache reads at 0.1x, 900K tokens means that much every turn, and Korean uses
      1.66x the tokens for the same conversation.
    - **Quality can actually get worse.** Very long contexts dilute attention, so a well-compressed
      260K-token session can produce better documents than a 900K-token session.
    - Bedrock's billing above 200k differs per account (its pricing is separate from
      first-party).

    In other words, this is a switch the deployment sets after looking at cost. That is also why it is
    not a per-project field — it is not the kind of value a workshop participant should judge while
    creating a project, and if it varied per project the field would spread into the manifest, restore,
    registry, and even the creation screen.
    """
    return os.environ.get(LONG_CONTEXT_ENV, "").strip().lower() in _TRUTHY


def cli_model_id(model_id: str | None) -> str | None:
    """The value to put in the CLI's `ANTHROPIC_MODEL`. When off, the argument as is.

    **`[1m]` is the CLI's alias format, not a Bedrock model id.** So this assembly must not go
    into `app.project_model`: that function also flows into `BedrockModel(model_id=...)` on the
    questionnaire generation path (app.questionnaire_agent_factory), and brackets there make
    Bedrock throw a ValidationException — i.e., only questionnaire generation breaks silently. The
    only places to attach it are the two factories that launch the CLI
    (`driver_factory`, `proto_session_factory`).

    Why this suffix is needed on Bedrock: in the bundled CLI's (2.1.231) model table,
    `claude-opus-5` is `context:{window:1e6, native_1m:true, supports_1m_beta:true}`
    but **has no `native_1m_3p`.** That check function does, for third-party providers,
    `case "bedrock": return native_1m_3p?.bedrock === true`, so on Bedrock the only model that
    gets native 1M is `claude-sonnet-5`, which has `native_1m_3p:{bedrock,vertex,foundry}`.
    Opus needs the beta turned on, and `[1m]` turns it on.

    If the model is None, returns None — when the driver receives None it does not set
    ANTHROPIC_MODEL and falls back to the CLI default (the last slot in app.project_model).
    Attaching a suffix to a missing value would break that fallback.

    **Which models accept this suffix was measured (2026-08-13, ap-northeast-2).**
    This switch is turned on per deployment, so every model in the catalog must accept it:
    model_catalog's 4 seeds (`claude-opus-5`, `claude-opus-4-6-v1`,
    `claude-sonnet-5`, `claude-sonnet-4-6`) and the deployment fallback (`claude-opus-4-8`)
    all responded normally with `[1m]` attached.

    Recording something that once caused confusion: `global.anthropic.claude-opus-4-6[1m]` gives 400
    "provided model identifier is invalid", but **not because of the suffix** —
    that id without `-v1` is itself invalid (calling it without the suffix gives the same 400). That is why
    the catalog seed uses `-v1`, and `...-4-6-v1[1m]` works. When registering a new model in the
    catalog, it is right to call it once in the suffixed form.
    """
    if model_id is None or not long_context_enabled():
        return model_id
    if model_id.endswith(_LONG_CONTEXT_SUFFIX):
        # Idempotent: some deployments may already have the suffix baked into the env default.
        return model_id
    return f"{model_id}{_LONG_CONTEXT_SUFFIX}"


def auto_compact_window() -> str | None:
    """The value to put in `CLAUDE_CODE_AUTO_COMPACT_WINDOW`. None if unset.

    It returns a string because the destination is the subprocess env — so callers do not
    have to str() it again.

    Out-of-range or non-numeric values are **warned about and become None**. Passing them through
    makes the CLI reject the settings, and that rejection does not show up in our logs, so "why is it
    still compacting at 260K" could not be traced. Logging a warning here makes typos visible in the
    deployment log.
    """
    raw = os.environ.get(AUTO_COMPACT_WINDOW_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s=%r is not an integer — ignoring",
                     AUTO_COMPACT_WINDOW_ENV, raw)
        return None
    if not _WINDOW_MIN <= value <= _WINDOW_MAX:
        _log.warning("%s=%d is outside the CLI's accepted range %d..%d — ignoring",
                     AUTO_COMPACT_WINDOW_ENV, value, _WINDOW_MIN, _WINDOW_MAX)
        return None
    return str(value)


#: The bundled CLI's background execution switch. When on, subagents (`Agent`), Bash's
#: `run_in_background`, and automatic background switching are all turned off, and work finishes within the turn that requested it.
DISABLE_BACKGROUND_TASKS_ENV = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"


def cli_context_env() -> dict[str, str]:
    """Shared env to add to the CLI subprocess.

    Both client factories merge this into `env`. It returns a dict so callers do not have to
    rewrite "if unset, do not add the key at all" every time — putting in an empty string makes
    the CLI read it as a value.

    **Background execution is always off.** Both screens follow a single turn and close the stream
    on `done` (openStream in frontend/lib/api/sse.ts), so if a turn ends before the work it requested,
    the remaining progress never reaches the screen and the user's next message lands on top of
    agents that are still modifying the workspace. Parallelism is unchanged — multiple `Agent`
    calls in one message run concurrently even in the foreground.

    Why an engine switch rather than a gate that inspects tool input: in the bundled CLI (2.1.292), `Agent`
    **runs in the background when `run_in_background` is omitted**, and that default changes with the SDK
    version (a dependency with only a lower bound in pyproject). This switch runs it in the foreground both
    when the key is absent and when the model passes `true` (observed, SDK 0.2.164). Drift where the switch
    name disappears from the bundle is caught by tests/test_sdk_available.py.
    """
    env = {DISABLE_BACKGROUND_TASKS_ENV: "1"}
    window = auto_compact_window()
    if window:
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = window
    return env


#: The bundled CLI's debug log switch. Off by default — turn it on only when diagnosing.
CLI_DEBUG_ENV = "AIPDS_SDK_CLI_DEBUG"


def cli_debug_args(config_dir: str) -> dict[str, str | None]:
    """The `--debug-file` argument to attach to the bundled CLI. Empty dict when off. Goes to the SDK's `extra_args`.

    **Whose log is it.** We use the Claude Agent SDK (Python), but the SDK does not run the
    agent loop inside Python — it launches the Claude Code CLI binary shipped in the package
    (`claude_agent_sdk/_bundled/claude`) as a subprocess and talks to it over stdio. Model calls and
    tool execution (including Bash) happen in that process, and `extra_args` are the command-line
    arguments the SDK attaches when launching it. This log belongs to that process.

    **Why it exists (2026-10-07).** Since the execution wrapper was introduced, on the first turn of
    every new project the first two complex Bash calls stall for exactly 12 seconds each **before**
    running (ws-g, ws-c, ws-e, test3). Reproductions on the production instance that matched the
    sandbox, trampoline, and even SDK hooks were all fast. The bundled CLI records that interval
    separately in the debug log
    (`[Stall] tool_dispatch_start … permissionDecisionMs=`, `Slow PreToolUse hooks`,
    `Slow permission decision`) — that log from a real session pins down the cause.

    **Why it is off by default.** Debug logs are verbose and contain parts of the session content. Turn it on
    when diagnosing, then turn it off and delete the file when done.

    **The location is this project's config dir.** That tree (BindPaths) is where the execution wrapper's
    sandbox can write, and it is deleted along with the project. The file name includes a timestamp
    because a new CLI process starts for each connection, and appending to the same file would make it
    impossible to tell which session a line belongs to.
    """
    if os.environ.get(CLI_DEBUG_ENV, "").strip().lower() not in _TRUTHY:
        return {}
    import time
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return {"debug-file": f"{config_dir}/cli-debug-{stamp}.log"}
