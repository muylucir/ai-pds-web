# backend/aipds/cli_settings.py -- the context settings passed to the bundled CLI.
#
# Two agents (the Discovery driver and the prototype builder) spawn the same CLI as a
# subprocess, so both use the same values. That is why there is one place that builds
# them -- with only one side switched on, the same project gets late compaction in
# Discovery and early compaction in a build, an asymmetry nobody can explain.
#
# **Why this file exists (measured 2026-08-13).** While chasing why a Korean project's
# later documents were thinner than an English one's, we ran into compaction. A real
# build session (claude-opus-4-8) had its context summarised from **264,040 to 53,375
# tokens** -- it is cut at 264k, nowhere near 1M. Documents written after that come from
# the summary rather than the evidence, so they thin out as the stages go on. Korean
# spends 1.66x the tokens for the same content, so it reaches that point 40% earlier.
#
# The dominant cause was not this but the absence of a length bar (the depth bar in
# discovery-config/CLAUDE.md and the length clause in
# agent/workspace_rules.LANGUAGE_DIRECTIVES); what this file addresses is the
# **amplifier**. Both have to be fixed for the later documents to recover.
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

#: How a boolean env is read. The same discipline as _TRUTHY in
#: routes/proto_public.py.
_TRUTHY = {"1", "true", "yes", "on"}

#: Whether to enable the 1M context. Off by default -- turning it on is not free (see
#: the docstring below).
LONG_CONTEXT_ENV = "AIPDS_LONG_CONTEXT"

#: The context size (in tokens) at which auto-compaction fires. Unset, the CLI default.
AUTO_COMPACT_WINDOW_ENV = "AIPDS_AUTO_COMPACT_WINDOW"

#: The range the CLI accepts. The value comes from the bundled CLI's (2.1.231) settings
#: schema (`autoCompactWindow: int().min(1e5).max(1e6)`). Passing something outside it
#: makes the CLI reject the setting, and that rejection never reaches our log, so it is
#: blocked here.
_WINDOW_MIN = 100_000
_WINDOW_MAX = 1_000_000

#: The suffix the bundled CLI accepts as a 1M-context alias. The `opus[1m]` and
#: `sonnet[1m]` forms are in the CLI's official alias list, and internally they enable the
#: `context-1m-2025-08-07` beta.
_LONG_CONTEXT_SUFFIX = "[1m]"


def long_context_enabled() -> bool:
    """Whether to enable the 1M context. **Off by default.**

    The default is off because turning it on is not strictly an upgrade:

    - **Per-turn cost** rises. Later compaction means the whole history is resent every
      turn. Even at 0.1x for a cache read, 900k tokens is 900k every turn, and Korean
      spends 1.66x the tokens on the same conversation.
    - **Quality can get worse.** A very long context dilutes attention, so a well
      compacted 264k-token session can produce better documents than a 900k-token one.
    - Bedrock's pricing above 200k differs per account (its rate structure is separate
      from first-party).

    So this is a switch a deployment sets after looking at cost. It is not a per-project
    field for the same reason -- it is not the kind of value a workshop participant judges
    while creating a project, and making it per-project would spread the field into the
    manifest, the restore path, the registry and the creation screen.
    """
    return os.environ.get(LONG_CONTEXT_ENV, "").strip().lower() in _TRUTHY


def cli_model_id(model_id: str | None) -> str | None:
    """The value to put in the CLI's `ANTHROPIC_MODEL`. With the switch off, the argument
    unchanged.

    **`[1m]` is a CLI alias form, not a Bedrock model id.** That is why this assembly must
    not go into `app.project_model`: that function also flows into
    `BedrockModel(model_id=...)` on the survey generation path
    (app.questionnaire_agent_factory), and a bracket there makes Bedrock throw a
    ValidationException -- meaning survey generation alone breaks, quietly. The only places
    it is attached are the two factories that spawn the CLI (`driver_factory` and
    `proto_session_factory`).

    Why the suffix is needed on Bedrock: in the bundled CLI's (2.1.231) model table,
    `claude-opus-5` has `context:{window:1e6, native_1m:true, supports_1m_beta:true}` but
    **no `native_1m_3p`.** For a third-party provider that decision function reads
    `case "bedrock": return native_1m_3p?.bedrock === true`, so the only model that gets
    native 1M on Bedrock is `claude-sonnet-5`, which has
    `native_1m_3p:{bedrock,vertex,foundry}`. Opus needs the beta enabled, and `[1m]`
    enables it.

    With a None model it returns None -- given None, the driver does not set
    ANTHROPIC_MODEL and falls through to the CLI default (the last slot in
    app.project_model). Appending a suffix to a missing value would break that fallback.

    **Which models accept this suffix was measured (2026-08-13, ap-northeast-2).** This
    switch is enabled per deployment, so every model in the catalogue has to accept it:
    model_catalog's four seeds (`claude-opus-5`, `claude-opus-4-6-v1`, `claude-sonnet-5`,
    `claude-sonnet-4-6`) and the deployment fallback (`claude-opus-4-8`) all responded
    normally with `[1m]` attached.

    One thing that caused confusion, recorded here:
    `global.anthropic.claude-opus-4-6[1m]` returns a 400 "provided model identifier is
    invalid", and **the suffix is not the reason** -- that id without `-v1` is itself
    invalid (the same 400 comes back without the suffix). That is why the catalogue seed
    uses `-v1`, and `...-4-6-v1[1m]` is fine. When registering a new model in the
    catalogue, the right move is to call it once with the suffix attached.
    """
    if model_id is None or not long_context_enabled():
        return model_id
    if model_id.endswith(_LONG_CONTEXT_SUFFIX):
        # Idempotent: a deployment may already have the suffix baked into its env
        # default.
        return model_id
    return f"{model_id}{_LONG_CONTEXT_SUFFIX}"


def auto_compact_window() -> str | None:
    """The value to put in `CLAUDE_CODE_AUTO_COMPACT_WINDOW`. None when unset.

    It returns a string because the destination is a subprocess env -- so the caller does
    not have to str() it again.

    A value out of range or not a number becomes **a warning and None**. Passed through,
    the CLI rejects the setting, and that rejection never reaches our log, leaving "why is
    it still compacting at 264k" untraceable. A warning here makes the typo visible in the
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


def cli_context_env() -> dict[str, str]:
    """The context-related env to add to the CLI subprocess. An empty dict when there is
    none.

    Both client factories merge this into `env`. It returns a dict so that the callers do
    not each have to re-implement "when unset, do not add the key at all" -- an empty
    string would be read by the CLI as a value.
    """
    window = auto_compact_window()
    return {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": window} if window else {}
