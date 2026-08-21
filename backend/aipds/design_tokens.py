"""Extract design tokens from a prose-only DESIGN.md.

**Why it exists (measured 2026-08-19).** The ```tokens fence is a convention that exists only
in our own template (`design_profile.TEMPLATE_MD`). A DESIGN.md authored elsewhere does not
have it, and that is the real input -- the deployed profile (a 37KB design system document) had
its role-to-colour mapping in both prose and tables, but with no fence it became `tokens={}` and
the generated theme CSS went out with zero variables. It failed not because the information was
absent but because it was not where the parser reads.

**This module has no parser of its own.** It feeds the model's response straight into
`parse_design_md` (`_FENCE_OPEN` finds the first fence anywhere in the text, so it parses even
when the model adds commentary). That way hex, length and typeface validation and the
whitelist all go through **the same code** as the hand-written case, and fixing the parser
carries this path along with it. A separate parser for responses would let the two diverge, and
the one that diverges would be the quiet one.

It knows nothing about S3 or FastAPI -- the only thing injected is an `async (prompt) -> str`
(the shape `app.questionnaire_agent_factory` already produces).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from aipds.design_profile import (ALLOWED_TOKENS, COLOR_TOKENS,
                                       FONT_TOKENS, LENGTH_TOKENS,
                                       DesignProfileError, parse_design_md)

_log = logging.getLogger(__name__)

#: A one-shot caller that takes a prompt and returns the response string.
Caller = Callable[[str], Awaitable[str]]

_FENCE = "```tokens"

#: The first attempt plus one retry. It goes no higher because this call is holding an
#: administrator's upload request -- a failure degrades to a zero-token warning, and that state
#: is valid too.
_MAX_ATTEMPTS = 2


def has_fence(text: str) -> bool:
    return any(line.strip() == _FENCE
               for line in text.replace("\r\n", "\n").split("\n"))


def render_fence(tokens: dict[str, str]) -> str:
    """Tokens as a ```tokens block. `parse_design_md` has to be able to read it back."""
    lines = [_FENCE] + [f"{k}: {v}" for k, v in tokens.items()] + ["```"]
    return "\n".join(lines) + "\n"


def inject_fence(markdown: str, tokens: dict[str, str]) -> str:
    """Plant the block in the original -- right after the first heading, or at the very top if
    there is none.

    This is how the stored object stays a single "original markdown plus metadata" (no derived
    value is stored separately -- the design_profile.py module docstring). It is also where an
    administrator can see and fix it directly in the downloaded file.

    **Not one byte of the prose changes.** So no blank line is appended and it attaches on the
    line right after the heading -- the parser strips only the fence lines and joins the rest,
    so a blank line added here would remain in the middle of the prose (measured: a test caught
    this condition).
    """
    fence = render_fence(tokens)
    lines = markdown.replace("\r\n", "\n").split("\n")
    idx = next((i for i, line in enumerate(lines) if line.startswith("#")), None)
    if idx is None:
        return fence + markdown
    return "\n".join(lines[:idx + 1]) + "\n" + fence + "\n".join(lines[idx + 1:])


def _value_rules() -> str:
    """Build the value-format description from the whitelist -- when a token group changes, the
    prompt changes with it. Written by hand, that sentence becomes a second truth and goes stale
    quietly."""
    return "\n".join([
        f"- colours ({', '.join(COLOR_TOKENS)}): `#rrggbb` or `#rgb` only.",
        f"- lengths ({', '.join(LENGTH_TOKENS)}): a single value like "
        f"`0.75rem` or `12px`.",
        f"- fonts ({', '.join(FONT_TOKENS)}): a font family name.",
    ])


def _prompt(markdown: str) -> str:
    """The extraction instruction. Written in English -- this output is a block a machine reads
    back rather than a sentence a person reads, and the input document's language can be
    anything."""
    return f"""You are extracting design tokens from a brand design document.

Return ONE fenced block and nothing else that looks like one:

```tokens
key: value
```

Rules:
- Allowed keys, and no others: {', '.join(ALLOWED_TOKENS)}
{_value_rules()}
- **A value that is not already in the accepted form above is omitted, never
  converted.** The accepted forms are the whole of what this format can carry, so
  anything else — a colour written some other way, a value built from a function
  or a variable, a gradient, a list — is simply left out. Do not translate it and
  do not approximate it.
- **Follow the ROLE the document gives each value.** Read how the document itself
  labels and uses a value; its own naming is the authority. A document that calls
  something its primary colour is telling you `primary`; a document that instead
  names a colour by where it is used is telling you that use.
- **Where the document gives one role several values, omit that key.** This is
  the honest answer whenever the document does not resolve it — several corner
  radii for different components with none stated as the general rule, two
  surface colours alternating with neither called the default. Omit rather than
  pick.
- Omit any key the document does not answer. Omitted keys keep the shadcn
  default, which is the correct outcome — never invent a value to fill a slot.
- **Never move a colour from one role into another to fill a gap.** If a role's
  value cannot be expressed here, omit that key. Substituting a different colour,
  such as the brand colour, is worse than omitting: `foreground` is the colour of
  every line of text on the screen, so a brand colour there tints the whole UI.
- Set `font_mono` only if the document names a font for code or monospaced text.
  A display or handwriting font is not a monospace font.
- Where a font is named that a browser will not have — an in-house or licensed
  family — use the fallback the document gives for it. If it gives none, omit.
- Take a value from a font stack or a variable definition only when it is stated
  plainly enough to copy; the first named family of a stack is such a value.

The document follows.

---

{markdown}"""


def _retry_prompt(markdown: str, reply: str, problem: str) -> str:
    """The retry. It carries the parser's own message verbatim -- that message, with its line
    number in it, is the only basis on which the model can fix things itself."""
    return (f"{_prompt(markdown)}\n\n---\n\nYour previous answer was rejected.\n\n"
            f"You answered:\n{reply}\n\nThe parser said: {problem}\n\n"
            "Return the corrected block only.")


async def extract_tokens(
        markdown: str,
        call: Caller | None) -> tuple[dict[str, str], list[str]]:
    """(tokens, warnings). It does not raise -- a failed extraction must not block the upload.

    When the original has a fence, **the model is not called**: the fence is authoritative, and
    the hand-written administrator path must not differ by a single character from what it was.
    A `DesignProfileError` on that path propagates as before (the route translates it into a
    400).

    With a None `call` (the deployment has no ANTHROPIC_MODEL) extraction is skipped and only
    the warning is returned -- applying the prose alone is a valid state too.
    """
    if has_fence(markdown):
        return parse_design_md(markdown)[0], []
    if call is None:
        return {}, ["no model is configured, so no tokens could be read "
                    "from the document"]

    prompt = _prompt(markdown)
    problem = "no attempt was made"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        reply = await call(prompt)
        # Handing a fence-less response to the parser returns ({}, all the prose) quietly --
        # which reads as "extraction succeeded, zero tokens" and reproduces the very bug being
        # fixed here.
        if not has_fence(reply):
            problem = "the reply contained no ```tokens block"
        else:
            try:
                tokens, _ = parse_design_md(reply)
            except DesignProfileError as exc:
                problem = str(exc)
            else:
                if tokens:
                    return tokens, []
                problem = "the ```tokens block was empty"
        _log.warning("design token extraction rejected (attempt %d/%d): %s",
                     attempt, _MAX_ATTEMPTS, problem)
        prompt = _retry_prompt(markdown, reply, problem)

    return {}, [f"could not read design tokens from the document: {problem}"]
