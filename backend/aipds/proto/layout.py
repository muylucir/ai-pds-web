# backend/aipds/proto/layout.py -- the sole owner of the prototype output layout.
#
# **Why this module exists (2026-08-16).** The Prototypes tab had exactly one rule for
# making a card, `prototypes/{slug}/PROTOTYPE-{slug}.md`, so a session that ran normally to
# completion through Path A.1 (Envision-derived, a single prototype) produced no cards at
# all (keumkang-v5 was in that state).
#
# Upstream has two layouts and both are legitimate:
#
#   Path B (use-case priority)     3  prototypes/{slug}/PROTOTYPE-{slug}.md
#   Path A.1 (Envision-derived)    1  prototype/prototype-spec.md
#
# All seven outputs `prototype-validation.md` declares are the singular `prototype/` (lines
# 556-562), and the one line in that document where `PROTOTYPE-` appears (line 16) is an
# **entry condition**, "if it already exists, go to the build". A single prototype has
# nothing to distinguish, so there is nothing for a slug to be. The tree diagram
# (core-workflow.md:333) marks `prototypes/` as "All paths", but the procedure that directly
# specifies this path is more concrete than the overview, so it wins.
#
# `rule/` is data and is not edited (edits would quietly disappear on the next resync). What
# needed fixing was our path assumption.
#
# **Why one module.** "id -> spec path" was duplicated as a hardcoded f-string in four
# places (routes/prototypes, routes/surveys, proto/session, survey/store). The cost that was
# invisible while there was one layout came due when there were two -- the same shape as
# pathsafe.workspace_relative having been duplicated three times, and four copies of a rule
# means four chances for one of them to drift into a hole.
#
# **It stays pure functions.** Probing S3 to discover the layout would be more "accurate",
# but it adds a round trip to every hot path and is invasive because places that use a
# synchronous helper -- `proto/session._spec_key`, for instance -- would have to become
# async. Handling the singular layout as one reserved-id branch finishes without any IO.
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

#: The subtree both layouts live in. This is the prefix passed to `s3.list()`.
DISCOVERY_PREFIX = "aiplc-docs/discovery/"

#: The id of the single prototype.
#:
#: **Why the directory name is used verbatim.** The id-to-path correspondence has to be
#: self-evident -- seeing `prototypes/prototype/bundle/` (build state) in a log or an S3 key
#: reads immediately as that prototype in `discovery/prototype/`. There is no mapping to
#: memorise.
#:
#: **Why not a reserved word such as `_prototype`.** The underscore is outside the slug
#: character class (`[a-z0-9-]`), which makes it look "structurally impossible to collide",
#: but that character class is only a rule the agent follows
#: (prototype-context-generation.md) and our code does not enforce it -- `_SLUGGED_RE`'s
#: capture accepts any segment. The assumption that the agent follows the rules has been
#: broken by measurement (on the same day, both the slug and the encoding instructions were
#: ignored), so no safety is built on top of it. Collisions are prevented **in code** by the
#: guard in `discover` below.
SINGLE_ID = "prototype"

#: Path A.1's spec path. The value declared by `prototype-validation.md:557`.
SINGLE_SPEC_KEY = "aiplc-docs/discovery/prototype/prototype-spec.md"

#: Path B's spec path. The backreference is load-bearing -- the directory name and the
#: filename suffix have to match (the upstream convention). A file where they do not is not
#: a card.
_SLUGGED_RE = re.compile(
    r"^aiplc-docs/discovery/prototypes/([^/]+)/PROTOTYPE-\1\.md$")


def _id_for(key: str) -> str | None:
    """The prototype's id if this S3 key is a spec, else None."""
    if key == SINGLE_SPEC_KEY:
        return SINGLE_ID
    match = _SLUGGED_RE.match(key)
    return match.group(1) if match else None


def discover(keys: list[str]) -> dict[str, str]:
    """A list of S3 keys -> {id: spec path}. Keys that are not specs are ignored.

    **It guarantees id uniqueness.** The earlier implementation overwrote with
    `slugs[id] = key`, so when two specs claimed one id a card disappeared quietly -- which
    one survived depended on `s3.list()`'s iteration order, making it a wrong result with no
    error.

    On a collision **the slugged one is kept**: upstream marks `PROTOTYPE-{slug}.md` as
    ★ Shareable and both the build and the surveys key off that file, so it is likely to
    have references attached already. And that choice has to be independent of iteration
    order to be a guard -- the code below is deterministic without sorting the keys (there is
    exactly one singular key, so a priority comparison suffices).

    The loser is written into a WARNING **with both keys**. "One card is missing" has to be
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
        # The slugged layout wins. There is only ever one singular key, so "of the two,
        # the singular one loses" decides it independently of order.
        winner, loser = ((key, current) if current == SINGLE_SPEC_KEY
                         else (current, key))
        found[pid] = winner
        _log.warning(
            "prototype id %r claimed by two specs — keeping %s, skipping %s",
            pid, winner, loser)
    return found


def spec_key(prototype_id: str) -> str:
    """id -> spec path. It has to round-trip with the ids `discover` returned.

    If they diverge, the card appears but the build and the surveys cannot find the spec --
    tests/test_proto_layout.py pins that round trip.
    """
    if prototype_id == SINGLE_ID:
        return SINGLE_SPEC_KEY
    return (f"aiplc-docs/discovery/prototypes/{prototype_id}"
            f"/PROTOTYPE-{prototype_id}.md")


def artifact_dir(prototype_id: str) -> str:
    """This prototype's output directory (where the questionnaire and the like go).

    It returns **the same directory** as the spec. Fixing only the read path and leaving the
    write path would put a singular prototype's questionnaire alone in a
    `prototypes/prototype/` tree the agent never writes to, and the deletion and archive
    paths would forget that tree.
    """
    if prototype_id == SINGLE_ID:
        return "aiplc-docs/discovery/prototype"
    return f"aiplc-docs/discovery/prototypes/{prototype_id}"
