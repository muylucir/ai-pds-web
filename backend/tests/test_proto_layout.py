# backend/tests/test_proto_layout.py — the two prototype artifact layouts.
#
# **Why this module exists (the misjudgment of 2026-08-16).** The Prototypes tab had only
# **one** rule for building cards: `prototypes/{slug}/PROTOTYPE-{slug}.md`. So a session
# that completed normally through Path A.1 (derived from Envision, single prototype)
# produced not a single card — ws-a-v5 is in that state.
#
# At first we concluded that "upstream Path A.1 forgot to create the slug file" and tried
# to make the agent write that file. **That conclusion was wrong.** All 7 artifacts that
# `prototype-validation.md` declares are under the singular `prototype/` (lines 556-562 of
# that document), and the only line where `PROTOTYPE-` appears (line 16) is an **entry
# condition**: "if it already exists, go to build". A single prototype has nothing to
# distinguish it from, so there is nothing to become a slug.
#
#   Path B (use-case priority)    3   prototypes/{slug}/PROTOTYPE-{slug}.md  slug required
#   Path A.1 (Envision-derived)   1   prototype/prototype-spec.md            no slug
#
# The upstream tree diagram (core-workflow.md:333) labels `prototypes/` as "All paths",
# but the procedure document that directly specifies the path is more concrete than the
# overview, so it wins. `rule/` is data and is not modified — what needs fixing is our
# path assumption.
#
# This module **solely owns** the layout. Previously "slug → spec path" was duplicated as
# a hardcoded f-string in four places (routes/prototypes, routes/surveys, proto/session,
# survey/store), and once there were two layouts that duplication came due as a cost —
# the same shape as pathsafe.workspace_relative having been duplicated three times.
from __future__ import annotations

import logging

from aipds.proto.layout import (SINGLE_ID, SINGLE_SPEC_KEY, artifact_dir,
                                     discover, spec_key)

SLUGGED = "aiplc-docs/discovery/prototypes/maint-support/PROTOTYPE-maint-support.md"


# ---- discover: recognizes both layouts ----

def test_discovers_the_slugged_layout():
    assert discover([SLUGGED]) == {"maint-support": SLUGGED}


def test_discovers_the_single_prototype_layout():
    """**This case is why this module exists.** ws-a-v5 was in this state and had 0 cards."""
    assert discover([SINGLE_SPEC_KEY]) == {SINGLE_ID: SINGLE_SPEC_KEY}


def test_both_layouts_coexist():
    """A project that made 3 via Path B and then ran Path A.1 has 4 specs —
    4 cards is correct."""
    keys = [SINGLE_SPEC_KEY] + [
        f"aiplc-docs/discovery/prototypes/{s}/PROTOTYPE-{s}.md"
        for s in ("a-one", "b-two", "c-three")]
    got = discover(keys)
    assert set(got) == {SINGLE_ID, "a-one", "b-two", "c-three"}


def test_ignores_everything_else():
    # Other artifacts in the same tree must not become cards.
    for key in (
        "aiplc-docs/discovery/prototype/design-context.md",
        "aiplc-docs/discovery/prototype/build-instructions.md",
        "aiplc-docs/discovery/prototypes/x/design-context.md",
        # Directory name and file name disagree — violates the upstream convention, so not a card.
        "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-bar.md",
        # Look-alike prefixes. Comparing as strings rather than segments lets these through.
        "aiplc-docs/discovery/prototypes-backup/x/PROTOTYPE-x.md",
        "aiplc-docs/discovery/prototype-old/prototype-spec.md",
    ):
        assert discover([key]) == {}, key


def test_discovery_is_order_independent():
    keys = [SINGLE_SPEC_KEY, SLUGGED]
    assert discover(keys) == discover(list(reversed(keys)))


# ---- id uniqueness guard ----

def test_a_colliding_id_does_not_silently_replace_the_other(caplog):
    """**Silence is the reason for this guard.**

    The old implementation overwrote with `slugs[id] = key`. When two specs claim one id,
    one card disappears, and which one survives depends on the `s3.list()` iteration
    order — a wrong result with no error.

    The id was set to `prototype` because the path mapping is self-evident. In exchange,
    collisions are prevented by **code**, not by a naming rule: the assumption that the
    agent would follow the slug sanitization rule broke twice today, and only what code
    prevented stayed prevented.
    """
    colliding = (f"aiplc-docs/discovery/prototypes/{SINGLE_ID}/"
                 f"PROTOTYPE-{SINGLE_ID}.md")

    with caplog.at_level(logging.WARNING):
        got = discover([SINGLE_SPEC_KEY, colliding])

    # Keep the slugged one — upstream marks it ★ Shareable and builds/surveys are keyed by
    # that file, so it is more likely to already have references attached.
    assert got == {SINGLE_ID: colliding}
    # Both keys must be logged so "one card is missing" can be diagnosed from one log line.
    assert any(SINGLE_SPEC_KEY in r.message and colliding in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_the_guard_is_order_independent():
    """If iteration order changes the result, it is not a guard."""
    colliding = (f"aiplc-docs/discovery/prototypes/{SINGLE_ID}/"
                 f"PROTOTYPE-{SINGLE_ID}.md")
    assert discover([SINGLE_SPEC_KEY, colliding]) == \
        discover([colliding, SINGLE_SPEC_KEY])


# ---- spec_key / artifact_dir: the resolver that four places call ----

def test_spec_key_resolves_both_layouts():
    assert spec_key("maint-support") == SLUGGED
    assert spec_key(SINGLE_ID) == SINGLE_SPEC_KEY


def test_artifact_dir_keeps_a_prototype_s_files_together():
    """The survey write path uses this. If only reads are fixed and writes are left, a
    singular prototype's survey ends up alone in a tree the agent does not even write to,
    and the delete/archive paths forget that tree."""
    assert artifact_dir("maint-support") == \
        "aiplc-docs/discovery/prototypes/maint-support"
    assert artifact_dir(SINGLE_ID) == "aiplc-docs/discovery/prototype"


def test_spec_key_round_trips_with_discover():
    """If the resolver and the discoverer disagree, the card shows up but the build cannot
    find the spec."""
    for key in (SLUGGED, SINGLE_SPEC_KEY):
        (found_id, found_key), = discover([key]).items()
        assert spec_key(found_id) == found_key


def test_the_single_id_is_the_directory_name():
    """The id ↔ path mapping must be self-evident — `prototypes/prototype/bundle/` in a log
    must read as that very prototype under `discovery/prototype/`."""
    assert SINGLE_ID == "prototype"
    assert SINGLE_SPEC_KEY == "aiplc-docs/discovery/prototype/prototype-spec.md"
