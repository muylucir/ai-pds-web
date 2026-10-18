# backend/tests/test_design_tokens.py
#
# Tests only the layer that extracts tokens from a prose-only DESIGN.md. The model call is an
# injected `async (prompt) -> str`, so a fake caller is used here — route wiring is in
# test_routes_design.py, and workspace application is in test_proto_design_sync.py.
#
# One discipline in this file: **there must be no code that parses the model response.** The
# response must pass straight through design_profile.parse_design_md, so the assertions here
# rely on the parser's contract (line-number errors, whitelist).
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aipds.design_profile import ALLOWED_TOKENS, parse_design_md
from aipds.design_tokens import (_prompt, extract_tokens, has_fence,
                                      inject_fence, render_fence)

FIXTURE = Path(__file__).parent / "fixtures" / "design-no-fence.md"
NO_FENCE = FIXTURE.read_text(encoding="utf-8")

WITH_FENCE = """# ACME

```tokens
primary: #5b2ea6
radius: 0.75rem
```

## 톤
여백을 넉넉히.
"""


class FakeCaller:
    """Returns responses in order and records the prompts received."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # If responses run out, repeat the last one — makes it easy to write "a model that
        # keeps failing".
        return self._replies[min(len(self.prompts) - 1, len(self._replies) - 1)]


#: A response to `design-no-fence.md` that **follows the rules**. It is a fake response for
#: plumbing tests, but it must be the answer the prompt instructs — if they diverge, a "good
#: answer" means a rule violation, and no test catches that state (it actually happened).
#:
#: The **absence** of `radius` is that rule: that document has three values for one role — card
#: 12px / button 50px / avatar 50% — and since the document did not pick a general rule, omitting
#: it is the honest answer. `foreground` is absent too — the body color is `rgba()`, which is
#: outside the allowed formats.
GOOD_REPLY = """문서에서 역할이 명시된 값만 골랐습니다.

```tokens
primary: #00754a
primary_foreground: #ffffff
background: #f2f0eb
destructive: #c82014
font_sans: Inter
```
"""


async def test_existing_fence_wins_without_calling_the_model():
    caller = FakeCaller(GOOD_REPLY)
    tokens, warnings = await extract_tokens(WITH_FENCE, caller)
    assert tokens == {"primary": "#5b2ea6", "radius": "0.75rem"}
    assert warnings == []
    # The fence is authoritative — the model is not inserted into values an admin wrote by hand.
    assert caller.prompts == []


async def test_extracts_tokens_from_a_prose_only_document():
    caller = FakeCaller(GOOD_REPLY)
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens["primary"] == "#00754a"
    assert tokens["font_sans"] == "Inter"
    assert warnings == []
    assert len(caller.prompts) == 1
    # Whether the document was actually sent — catches the mistake of sending only the prompt
    # and leaving out the document.
    assert "Riverbank Green" in caller.prompts[0]


async def test_reply_without_a_fence_is_not_silently_accepted():
    # parse_design_md silently returns ({}, the whole prose) when there is no fence. Trusting
    # that return as is would give "extraction succeeded, 0 tokens", reproducing exactly the bug
    # being fixed now.
    caller = FakeCaller("토큰을 찾지 못했습니다.")
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens == {}
    assert warnings
    assert len(caller.prompts) == 2  # asks back once


async def test_no_caller_at_all_is_a_warning_not_an_exception():
    # If the deployment has no ANTHROPIC_MODEL, there is no caller. The upload must continue even
    # then — applying only the prose is also a valid state.
    tokens, warnings = await extract_tokens(NO_FENCE, None)
    assert tokens == {}
    assert warnings


async def test_no_caller_still_reads_an_existing_fence():
    tokens, warnings = await extract_tokens(WITH_FENCE, None)
    assert tokens == {"primary": "#5b2ea6", "radius": "0.75rem"}
    assert warnings == []


async def test_invalid_value_is_retried_once_with_the_parser_message():
    bad = "```tokens\nprimary: rebeccapurple\n```\n"
    caller = FakeCaller(bad, GOOD_REPLY)
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens["primary"] == "#00754a"
    assert warnings == []
    assert len(caller.prompts) == 2
    # The ask-back prompt carries the line numbers the parser pointed out as is — it is the only
    # basis on which the model can fix itself.
    assert "line 2" in caller.prompts[1]
    assert "rebeccapurple" in caller.prompts[1]


async def test_gives_up_after_one_retry_and_warns():
    caller = FakeCaller("```tokens\nradius: 12\n```\n")
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens == {}
    assert warnings
    assert len(caller.prompts) == 2  # at most twice — does not hold up the upload


async def test_key_outside_the_whitelist_never_reaches_the_profile():
    caller = FakeCaller("```tokens\nbrand_color: #00754a\n```\n")
    tokens, _ = await extract_tokens(NO_FENCE, caller)
    assert tokens == {}


async def test_extracted_keys_are_all_allowed():
    caller = FakeCaller(GOOD_REPLY)
    tokens, _ = await extract_tokens(NO_FENCE, caller)
    assert set(tokens) <= set(ALLOWED_TOKENS)


def test_render_fence_round_trips_through_the_parser():
    tokens = {"primary": "#00754a", "radius": "0.75rem", "font_sans": "Inter"}
    parsed, prose = parse_design_md(render_fence(tokens))
    assert parsed == tokens
    assert prose == ""


def test_inject_fence_puts_the_block_after_the_first_heading():
    tokens = {"primary": "#00754a"}
    injected = inject_fence(NO_FENCE, tokens)
    lines = injected.split("\n")
    assert lines[0].startswith("# Design System")
    assert "```tokens" in "\n".join(lines[1:6])
    # The injected document is now indistinguishable from one a person wrote.
    assert parse_design_md(injected)[0] == tokens


def test_inject_fence_leaves_the_prose_untouched():
    before = parse_design_md(NO_FENCE)[1]
    after = parse_design_md(inject_fence(NO_FENCE, {"primary": "#00754a"}))[1]
    assert after == before


def test_inject_fence_without_a_heading_puts_the_block_first():
    injected = inject_fence("톤을 지켜라.\n", {"primary": "#00754a"})
    assert injected.startswith("```tokens")
    assert "톤을 지켜라." in parse_design_md(injected)[1]


# ---- Whether the prompt is biased toward one document ----
#
# 4 of the 5 judgment rules came straight from `design-no-fence.md`, and one of them carried that
# document's color value (`rgba(0,0,0,0.87)`) as a literal. If the tuning document and the
# validation document are the same, there is no evidence of generalization, and this feature's
# failures are quiet and global — if `primary` or `foreground` drifts, every prototype in every
# project ships in that color.
#
# The checks below do not measure extraction **quality** (there is no model). What they measure
# is one thing — that the instructions are not document-specific — and that is the part code can
# guard.
#
# **Quality was confirmed once with a real model (2026-08-20, global.anthropic.claude-opus-5,
# three fixtures).** The three documents produced different omissions for different reasons:
#
#   design-no-fence.md          foreground/radius omitted — body is rgba(), three radii
#   design-primary-labelled.md  foreground/radius extracted — body is hex, one radius
#   design-token-table.md       all oklch() omitted, only the first typeface of the stack
#
# The last line is the point of this commit: the old prompt that enumerated rejected notations
# (`rgba`/`hsl`) did not have `oklch()` in its list. Everything outside the allowed formats must
# be closed off as omit for it to hold even when document shapes differ.
#
# That check is not put into CI — real calls are nondeterministic and cost money. To reproduce,
# feed the fixtures to `extract_tokens` with a real caller
# (`app.design_token_extractor` shows how to build that caller).

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("design-*.md"))

#: Values that came out of any fixture. If the prompt has one of these, it was written for
#: that document.
_DOCUMENT_VALUES = ("rgba(0,0,0,0.87)", "SoDoSans", "JetBrains Mono", "Inter",
                    "Riverbank", "Harbour Blue", "oklch(", "Source Sans")

_HEX_VALUE = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")


def test_the_corpus_has_more_than_one_document_shape():
    """With a single fixture, that fixture becomes the spec."""
    assert len(FIXTURES) >= 3, [p.name for p in FIXTURES]


@pytest.mark.parametrize("needle", _DOCUMENT_VALUES)
def test_the_prompt_carries_no_value_from_any_document(needle):
    """If a specific document's values are baked into the instructions, the model looks for
    them as a pattern."""
    assert needle not in _prompt("(document)"), (
        f"{needle!r}가 프롬프트에 있다 — 부류로 말해야 한다")


def test_the_prompt_states_no_concrete_colour():
    """`#rrggbb` and `#rgb` remain because they are **format placeholders** (not hex digits).
    An actual hex value would plant the answer, not an example."""
    found = _HEX_VALUE.findall(_prompt("(document)"))
    assert found == [], found


def test_the_prompt_does_not_hardcode_a_role_resolution():
    """The spot that contradicted `follow the ROLE the document gives them`.

    "If the heading color and the CTA color differ, the CTA is primary" is one document's
    convention. Many design systems explicitly call the brand/heading color `Primary` and use a
    different color for buttons (`design-primary-labelled.md` is that opposite case) — in that
    document this sentence becomes an instruction to ignore the document's labels.
    """
    # Normalize whitespace before checking — the source has line breaks in it, so a literal
    # comparison did not catch "the CTA colour is" (which is why this check passed silently).
    prompt = " ".join(_prompt("(document)").split())
    assert "CTA colour is" not in prompt
    assert "ROLE the document gives" in prompt, "일반 규칙은 남아 있어야 한다"


def test_the_prompt_closes_the_world_instead_of_listing_rejects():
    """Enumerating notations to reject lets the missing ones through.

    `oklch()`, `color-mix()`, `lab()`, and `var(--x)` are the same kind as `rgba`/`hsl` but were
    not in the enumeration (`design-token-table.md` is an `oklch()` document). The allowed
    formats are already generated from the whitelist (`_value_rules`), so closing everything
    outside them as omit means the list cannot have holes.
    """
    prompt = _prompt("(document)")
    for enumerated in ("rgba", "hsl"):
        assert enumerated not in prompt, (
            f"{enumerated!r}를 열거한다 — 허용 형식 밖은 전부 omit이어야 한다")


def test_the_radius_rule_is_decidable():
    """The old wording was "whichever of cards and buttons dominates".

    dominance was undefined so it could not be judged, and `GOOD_REPLY` carried an answer that
    broke that rule (the card radius) as the "good answer" — the rule and the expectation
    disagreed and no test caught it.
    """
    assert "dominates" not in _prompt("(document)")


# ---- Whether the plumbing holds even when document shapes differ ----


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_every_fixture_is_a_no_fence_document(path):
    """This is the definition of this corpus — if there is a fence, the extraction path is not
    taken."""
    assert not has_fence(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_inject_fence_round_trips_on_every_shape(path):
    """The prose does not change by a single byte, and the planted tokens read back.

    A document that starts with front matter (`design-token-table.md`) is the reason for this
    check — `inject_fence` looks for "the first `#` line", so where the block goes in a shape
    where the heading is not the first line depends on the document's shape.
    """
    original = path.read_text(encoding="utf-8")
    tokens = {"primary": "#1b4f8a", "radius": "4px"}
    injected = inject_fence(original, tokens)

    read_tokens, prose = parse_design_md(injected)
    assert read_tokens == tokens
    assert prose == original.replace("\r\n", "\n").rstrip("\n")
