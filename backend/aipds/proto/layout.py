# backend/aipds/proto/layout.py — sole owner of the prototype artifact layout.
#
# **Why this module exists (2026-08-16).** The Prototypes tab had only one rule for making cards,
# `prototypes/{slug}/PROTOTYPE-{slug}.md`, so a session that completed normally on Path A.1 (derived
# from Envision, single prototype) produced no cards at all
# (ws-a-v5 was in that state).
#
# Upstream has two layouts, and both are legitimate:
#
#   Path B (use-case priority)        3  prototypes/{slug}/PROTOTYPE-{slug}.md
#   Path A.1 (derived from Envision)  1  prototype/prototype-spec.md
#
# The 7 artifacts `prototype-validation.md` declares are all under the singular `prototype/`
# (lines 556-562), and the only line in that document where `PROTOTYPE-` appears (line 16) is the **entry
# condition** "if it already exists, go to build". A single prototype has nothing to distinguish it from,
# so there is nothing to become a slug either. The tree diagram (core-workflow.md:333) labels `prototypes/`
# as "All paths", but the procedure that defines the path directly is more specific than the
# overview, so it wins.
#
# `rule/` is data, so it is not modified (modifications silently disappear on resync). What needed
# fixing was our path assumption.
#
# **Why one module.** "id → spec path" was duplicated as a hardcoded f-string in four places
# (routes/prototypes, routes/surveys, proto/session, survey/store).
# A cost invisible with one layout came due once there were two —
# the same shape as pathsafe.workspace_relative having been duplicated three times, and with four copies
# of a rule there are four chances for one of them to drift into a hole.
#
# **Kept as pure functions.** Probing S3 to work out the layout would be more "accurate", but it
# adds round trips on every hot path and is invasive, since places that use it as a sync helper such as
# `proto/session._spec_key` would have to become async. Handling the singular layout as a branch on one reserved id
# finishes without IO.
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

#: The subtree where both layouts live. The prefix passed to `s3.list()`.
DISCOVERY_PREFIX = "aiplc-docs/discovery/"

#: The id of the single prototype.
#:
#: **Why use the directory name as is.** The id ↔ path mapping must be self-evident — seeing
#: `prototypes/prototype/source/` (build source) in a log or S3 key reads immediately as
#: that prototype in `discovery/prototype/`. There is no mapping to memorize.
#:
#: **Why not a reserved word like `_prototype`.** The underscore is outside the slug character class
#: (`[a-z0-9-]`), so it looks "structurally collision-free", but that character class is only
#: a rule the agent follows (prototype-context-generation.md), and our code does not
#: enforce it — the capture in `_SLUGGED_RE` accepts any segment. The assumption that the agent will
#: follow the rules was broken by observation (the same day, both the slug and encoding instructions
#: were ignored), so safety is not built on it. Collisions are blocked **in code** by the guard
#: in `discover` below.
SINGLE_ID = "prototype"

#: Spec path for Path A.1. The value `prototype-validation.md:557` declares.
SINGLE_SPEC_KEY = "aiplc-docs/discovery/prototype/prototype-spec.md"

#: Spec path for Path B. The backreference is load-bearing — the directory name and the file name
#: suffix must match (upstream convention). A mismatched file is not a card.
_SLUGGED_RE = re.compile(
    r"^aiplc-docs/discovery/prototypes/([^/]+)/PROTOTYPE-\1\.md$")


def _id_for(key: str) -> str | None:
    """If this S3 key is a spec, the id of its prototype; otherwise None."""
    if key == SINGLE_SPEC_KEY:
        return SINGLE_ID
    match = _SLUGGED_RE.match(key)
    return match.group(1) if match else None


def discover(keys: list[str]) -> dict[str, str]:
    """S3 key list → {id: spec path}. Keys that are not specs are ignored.

    **Guarantees id uniqueness.** The old implementation overwrote with `slugs[id] = key`, and when two
    specs claimed one id, one card silently disappeared — which one survived depended on
    the iteration order of `s3.list()`, so it was a wrong result with no error.

    On collision **the slug side is kept**: upstream marks `PROTOTYPE-{slug}.md` as ★ Shareable
    and builds and surveys are keyed by that file, so references are likely already attached.
    And that choice must be independent of iteration order to be a guard — the code below is deterministic
    without sorting the keys (there is exactly one singular key, so a priority comparison is enough).

    The side that loses writes **both keys** to the WARNING. "One card is missing" must be
    diagnosable from a single log line.
    """
    found: dict[str, str] = {}
    for key in keys:
        pid = _id_for(key)
        if pid is None:
            continue
        current = found.get(pid)
        if current is None:
            found[pid] = key
            continue
        if current == key:
            continue
        # The slug layout wins. There is only one singular key, so "of the two, the singular one
        # loses" decides it independently of order.
        winner, loser = ((key, current) if current == SINGLE_SPEC_KEY
                         else (current, key))
        found[pid] = winner
        _log.warning(
            "prototype id %r claimed by two specs — keeping %s, skipping %s",
            pid, winner, loser)
    return found


def spec_key(prototype_id: str) -> str:
    """id → spec path. Must round-trip with the ids `discover` returns.

    If they disagree, the card appears but builds and surveys cannot find the spec —
    tests/test_proto_layout.py pins that round trip.
    """
    if prototype_id == SINGLE_ID:
        return SINGLE_SPEC_KEY
    return (f"aiplc-docs/discovery/prototypes/{prototype_id}"
            f"/PROTOTYPE-{prototype_id}.md")


def artifact_dir(prototype_id: str) -> str:
    """This prototype's artifact directory (where the questionnaire and the like go).

    Returns the **same directory** as the spec. Fixing only the read path and leaving writes would make a singular
    prototype's questionnaire appear alone in a `prototypes/prototype/` tree the agent never writes to,
    and the delete and archive paths would forget that tree.
    """
    if prototype_id == SINGLE_ID:
        return "aiplc-docs/discovery/prototype"
    return f"aiplc-docs/discovery/prototypes/{prototype_id}"


#: Name of the Step 3 build instructions. `prototype-validation.md:170` declares it with a singular path,
#: and it is the **builder's input**, combining the spec (Step 1) and the design-context answers (Step 2)
#: (line 244 of the same document, "The AI agent reads `build-instructions.md`"). Path B's rules do not
#: use this file — `PROTOTYPE-{slug}.md` is itself the build context.
BUILD_INSTRUCTIONS = "build-instructions.md"


def build_instructions_key(prototype_id: str) -> str:
    """id → build instructions path. Same directory as the spec."""
    return f"{artifact_dir(prototype_id)}/{BUILD_INSTRUCTIONS}"


#: Change history file name. A web-owned artifact — not in the upstream rules. It accumulates the
#: "what changed relative to the spec" that build sessions leave with `build_complete` (proto/session).
CHANGE_HISTORY = "change-history.md"


def change_history_key(prototype_id: str) -> str:
    """id → change history path. Same directory as the spec.

    The S3 source-of-truth copy and the copy in the build directory use the same path string (same as the instructions). Why the source-of-truth copy
    lives under `aiplc-docs/`: the Discovery workspace restores this tree, so the spec reconciliation pass
    just before Proceed (discovery-config/CLAUDE.md) reads this history without looking at the code. It is the same
    path by which the survey aggregate (`SURVEY_AGGREGATE`) reaches Step 6. Reset deletes the exact key
    (proto/session.purge_session_state).
    """
    return f"{artifact_dir(prototype_id)}/{CHANGE_HISTORY}"


#: Survey aggregate file name. A web-owned artifact — not in the upstream rules. "결과 취합" (Synthesize results)
#: rebuilds it from the responses and overwrites it each time, and reset deletes it (survey/store).
#:
#: **It must be a different file from the rules' `validation-results.md`.** That file is the **rules' artifact**
#: into which the agent writes its analysis in Step 6 and which Step 8's Part 2 cites as evidence.
#: If the web wrote the aggregate into the same file it would have two owners: a re-synthesis would overwrite the agent's
#: analysis with an empty template, and a reset would delete the evidence Part 2 cites (observed 2026-10-06
#: ws-b — the survey was open with the agent's analysis attached beneath the web aggregate). This file is
#: the "feedback file to import" that Step 5 allows, and `validation-results.md` is built by the agent
#: itself from reading it. That is why the Discovery gate blocks writes to this file
#: (agent/discovery_guard.web_owned_denial).
SURVEY_AGGREGATE = "survey-aggregate.md"


def survey_aggregate_key(prototype_id: str) -> str:
    """id → survey aggregate path. Same directory as the spec (same reason as the questionnaire copy)."""
    return f"{artifact_dir(prototype_id)}/{SURVEY_AGGREGATE}"
