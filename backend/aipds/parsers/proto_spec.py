# backend/aipds/parsers/proto_spec.py — spec document → human-readable prototype name.
#
# **Why it is needed.** Card titles on the Prototypes tab were the `slug`, and the slug for Path A.1 (derived from Envision,
# single prototype) is the constant `"prototype"` (`SINGLE_ID` in proto/layout.py).
# The judgment that a single prototype has nothing to distinguish it from, so there is nothing to become a slug, is
# right, but as a result **every Path A.1 project's card title was the literal "prototype"**.
# The product name existed only in the spec body and was not carried in the list response.
#
# **Why a pure function.** layout.py owns the paths, and this module only looks at the body read
# from that path — it does not branch on layout. The name lines of the two upstream templates differ only
# in label and have the same form, so accepting both labels removes any need to know the layout.
from __future__ import annotations

import re

#: It is a value carried in a single card-title line, so it is cut in the payload. The model sometimes writes
#: a sentence where the name goes (an observed habit), and CSS truncate only handles the display problem
#: after it has already arrived — the list response still grows.
MAX_NAME_CHARS = 120

#: The line where the name lives. The two upstream templates differ only in label:
#:
#:   Path A.1  `- **Product**: [From PR/FAQ headline]`  (prototype-validation.md:42)
#:   Path B    `**Use Case**: {Use Case Name}`          (prototype-md-format.md:28)
#:
#: **Labels come in two versions, one per language.** The template labels are English, but ko project artifacts come out
#: as `- **제품**:` — not by accident but as instructed behavior (the ko language convention in `agent/workspace_rules.py`:
#: "질문 문구·헤딩·**라벨**·선택지는 한국어로 옮긴다" (translate question wording, headings, **labels** and options into Korean)). The rule that accepted only
#: English was always None in ko, and since ko is the default, that was effectively everything —
#: observed (2026-09-02): all 4 specs in S3 use `**제품**`. Having code own the text the model reads and writes
#: in two versions per language is an existing convention in this repo (`agent/prompts.py`,
#: `proto/prompts.py`, `survey/builder.py`). The upstream ruleset is not touched.
#:
#: There is no observed sample of the Path B ko label. "Use Case" may stay as a technical term
#: (then the English version catches it), or the same convention may call for translating it, so the spelling
#: is not settled here, and the three common variants are all accepted.
#:
#: **Anchored to the line start.** So a sentence in the body quoting the label ("**Product**: 라고 적는다") is not
#: read as the name. The list marker (`- `) is used by Path A.1, so it is accepted optionally.
_NAME_LINE = re.compile(
    r"^(?:[-*]\s*)?\*\*(?:Product|Use Case"
    r"|제품|유즈케이스|유스케이스|사용 사례)\*\*:\s*(.*)$",
    re.MULTILINE)

#: The boundary between the name and the one-line description. 3 of the 4 observed specs use the form `제품명 — 설명` (product name — description)
#: (`트래블프렌드 — 여행 상품을 권하지 않는 AI 여행 친구`). The card title is the name's place,
#: so the description is dropped.
#:
#: **Length truncation does not substitute for this.** That description sometimes continues onto the next line
#: (observed: novadesk), and a rule that captures only to the end of the line turns the title into a sentence fragment (`… PM이 무엇을`),
#: and `MAX_NAME_CHARS` leaves a fragment a fragment. Where to cut is a boundary,
#: not a length. Only the em dash is accepted — a hyphen can be part of a name (`AI-PDS`).
_TAGLINE = re.compile(r"\s+—\s+")

#: Emphasis marks left in the value. The model sometimes bolds the value too, when the template says to bold only the label,
#: so this keeps `**` from showing up verbatim in the title.
_EMPHASIS = "*_ \t"

#: Opening brackets of unfilled template placeholders. Putting `[From PR/FAQ headline]` up as the title
#: is worse than the slug — the user reads it as not being their prototype.
_PLACEHOLDER_OPENERS = ("[", "{")


def spec_name(markdown: str) -> str | None:
    """Spec body → the name to use on the card. None if absent (the caller falls back to the slug).

    Why None rather than an empty string: "no name found" and "the name is an empty string" must be
    the same branch in the caller, and that branch must be on whether a value exists.

    The first match wins — it is document order, so it does not depend on s3 iteration order or dict order.
    """
    for match in _NAME_LINE.finditer(markdown):
        name = re.sub(r"\s+", " ", match.group(1)).strip(_EMPHASIS).strip()
        # Dropping the description comes before the placeholder check: `{Use Case Name} — 설명` must
        # still be a placeholder after cutting to be rejected.
        name = _TAGLINE.split(name, 1)[0].strip(_EMPHASIS).strip()
        if not name or name.startswith(_PLACEHOLDER_OPENERS):
            continue
        return name[:MAX_NAME_CHARS]
    return None
