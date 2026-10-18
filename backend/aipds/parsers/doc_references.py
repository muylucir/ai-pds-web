# backend/aipds/parsers/doc_references.py — artifacts a document points to that do not exist.
#
# **Why this exists (observed 2026-10-06).** The Discovery Document is the entry
# document for AI-DLC Inception, and it defers evidence to other artifacts
# ("전체 결과는 `prototype/validation-results.md`에 있다" -- "the full results are in
# ..."). In the drill bucket, two projects turned up where the file that reference
# points to did not exist (ws-l, test1111). The causes differed (already missing
# before the import / the bucket migration copied only a day-old snapshot), but the
# result was the same: a handoff document with broken links downloads with no warning.
# The upstream Step 8 template has the reference written whether or not the file
# exists, so blocking causes one by one will not close this -- this module surfaces it
# right before download.
#
# **Find by path shape, not by label.** ko projects translate the template labels
# ("전체 결과:", "상세:", "전체 명세는 … 있습니다"). Searching by wording always misses
# in ko.
#
# It does not fix anything. It only shows what is missing -- a person decides which
# side is wrong (the document or the file).
from __future__ import annotations

import re

from aipds.proto import layout

#: A path-like `.md` token. Placeholder characters (`{}*<>`) must be captured in the same
#: token so it can be discarded whole below -- excluding them would cut
#: `{slug}/design-context.md` down to `/design-context.md`, which looks like a real path.
#: The end is a negated ASCII word character rather than `\b`: Python's `\w` includes
#: Hangul, so there is no boundary between the `d` and `에` in `.md에`.
_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_./{}*<>-])[A-Za-z0-9_./{}*<>-]+\.md(?![A-Za-z0-9_])")

#: Markers of a template path (`prototypes/{slug}/PROTOTYPE-{slug}.md`) and of a glob.
_PLACEHOLDER = re.compile(r"[{}*<>]")

#: Files whose absence is normal. `change-history.md` is not created if nothing changed
#: after approval, and its absence means "the spec is unchanged" (the Spec reconciliation
#: section of discovery-config/CLAUDE.md). That was the sentence observed in ws-b:
#: "변경 이력(`change-history.md`)이 없으므로" ("since there is no change history").
_ABSENCE_IS_MEANINGFUL = frozenset({layout.CHANGE_HISTORY})

_DOCS_PREFIX = "aiplc-docs/"


def _normalize(ref: str) -> str:
    while ref.startswith("./"):
        ref = ref[2:]
    return ref[len(_DOCS_PREFIX):] if ref.startswith(_DOCS_PREFIX) else ref


def missing_references(text: str, artifact_paths: list[str]) -> list[str]:
    """The `.md` paths mentioned in `text` that are not in `artifact_paths`, in order of first appearance.

    `artifact_paths` are workspace-relative paths starting with `aiplc-docs/`. References
    use a different base in each document (`prototype/x.md`, `discovery/prototype/x.md`,
    `x.md`). So a reference counts as present if it matches some artifact's **path tail**
    segment by segment -- fixing a single base would make the other two all false
    positives.
    """
    known = [_normalize(p) for p in artifact_paths]
    missing: list[str] = []
    for match in _CANDIDATE.finditer(text):
        ref = match.group(0)
        if _PLACEHOLDER.search(ref) or ref.startswith("/"):
            continue  # A template path, or the tail of an absolute path/URL -- not an artifact reference
        name = _normalize(ref)
        if not name or name.rsplit("/", 1)[-1] in _ABSENCE_IS_MEANINGFUL:
            continue
        if any(k == name or k.endswith("/" + name) for k in known):
            continue
        if ref not in missing:
            missing.append(ref)
    return missing
