# backend/aipds/doc_revisions.py — the documents as they stood when the user handed over answers.
#
# **Why this module exists.** After a question round the agent revises the documents, and the user
# opens them again to check — and could not find what changed (workshop feedback, 2026-10). Each
# document is overwritten in place, so nothing kept the text the user had read before answering. The
# doc panel's `v2` is a counter (agent/reconcile.document_events) with no text behind it, and an
# approval record keeps only a hash. Without the earlier text there is nothing to compare.
#
# **The boundary is handing over answers** — the question form (`routes/answers.py`) and changing a
# settled answer on the handoff tab (`routes/handoff.py`). Those are the moments after which the agent
# rewrites documents because of what the user said, and both refuse a running turn first
# (`ensure_idle`), so the previous turn has already published everything to the source of truth: what
# is read here is what the user could see. Taking it at turn end instead would cut the user's one
# request into as many pieces as the turns it took.
#
# **Layout.** S3Store prepends `projects/{pid}/`, so these are project-relative keys:
#
#     revisions/rounds/{stamp}-{suffix}.json   {"at", "kind", "source", "documents": {path: sha256}}
#     revisions/blobs/{sha256}.md              a body, written once
#
# Bodies are stored by content, so a round costs only the documents that changed since some earlier
# round — that is what makes keeping every round affordable (retention is unlimited by decision).
# Rounds and bodies are never rewritten, which is also why reading them can be cached in-process.
#
# The scope is `is_document` — the set the doc panel follows — which includes the prototype specs
# under `aiplc-docs/discovery/`. `revisions/` is outside the workspace sync globs, so the agent never
# sees these copies, and project export carries them like any other project key (project_bundle).
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from aipds.agent.reconcile import is_document

_log = logging.getLogger(__name__)

ROUNDS_PREFIX = "revisions/rounds/"
BLOBS_PREFIX = "revisions/blobs/"

#: What handed the answers over. The screen names the baseline after it ("… 답변 전" / "… 답 변경 전").
KIND_ANSWERS = "answers"
KIND_REVISION = "revision"

#: Shape of a round id (the key's file stem). The id comes back from the browser, so it is checked
#: before it becomes part of a key.
_ROUND_ID = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{8}$")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Round:
    id: str
    at: str
    kind: str
    source: str
    documents: dict[str, str]


def _round_key(round_id: str) -> str:
    return f"{ROUNDS_PREFIX}{round_id}.json"


def _blob_key(digest: str) -> str:
    return f"{BLOBS_PREFIX}{digest}.md"


def _new_round_id(at: str) -> str:
    """UTC stamp + random suffix: sorts by time, and two rounds in one second do not collide.
    `at` is ISO-8601 UTC (`2026-10-27T05:20:11.123+00:00`); only the second-resolution part is kept."""
    stamp = at[:19].replace(":", "-") + "Z"
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


async def capture_round(runner, *, kind: str, source: str, at: str) -> str | None:
    """Store every document as it stands now, as one round. Returns the round id, or None if there
    were no documents yet (the first round of a project usually comes before any document)."""
    paths = [p for p in await runner.list_files("aiplc-docs/**/*.md") if is_document(p)]
    texts = await asyncio.gather(*(runner.read_file(p) for p in paths), return_exceptions=True)
    documents: dict[str, str] = {}
    bodies: dict[str, str] = {}
    for path, text in zip(paths, texts):
        if isinstance(text, BaseException):
            # Listed but gone (deleted in between), or unreadable: the round still holds the rest.
            _log.warning("revision capture skipped %s: %r", path, text)
            continue
        if not text.strip():
            continue
        digest = content_hash(text)
        documents[path] = digest
        bodies[digest] = text
    if not documents:
        return None
    # Bodies first, the round last: a round that is listed must be able to deliver every body it names.
    await asyncio.gather(*(runner.write_file_if_absent(_blob_key(d), body)
                           for d, body in bodies.items()))
    round_id = _new_round_id(at)
    await runner.write_file(_round_key(round_id), json.dumps({
        "at": at, "kind": kind, "source": source, "documents": documents,
    }, ensure_ascii=False))
    return round_id


async def capture_before_answers(runner, *, kind: str, source: str) -> None:
    """`capture_round` for the routes that hand answers over. A failure is logged, not raised: the
    user has already submitted, and losing one comparison is a smaller harm than losing the answers
    (the same stance as the state probe in routes/answers.py)."""
    try:
        await capture_round(runner, kind=kind, source=source,
                            at=datetime.now(timezone.utc).isoformat())
    except Exception:
        _log.exception("revision capture failed — the answers go on without a baseline")


#: Rounds already read, by (project, key). Rounds are never rewritten, so an entry cannot go stale;
#: the cap only bounds memory. Without it every open of the doc panel would GET every round again.
_ROUND_CACHE_MAX = 4096
_round_cache: OrderedDict[tuple[str, str], Round] = OrderedDict()


def _parse_round(round_id: str, raw: str) -> Round | None:
    try:
        data = json.loads(raw)
        documents = data["documents"]
        if not isinstance(documents, dict):
            return None
        return Round(id=round_id, at=str(data["at"]), kind=str(data.get("kind", KIND_ANSWERS)),
                     source=str(data.get("source", "")),
                     documents={str(k): str(v) for k, v in documents.items()})
    except (ValueError, KeyError, TypeError):
        return None


async def _load_round(runner, project_id: str, round_id: str) -> Round | None:
    key = _round_key(round_id)
    cached = _round_cache.get((project_id, key))
    if cached is not None:
        _round_cache.move_to_end((project_id, key))
        return cached
    try:
        parsed = _parse_round(round_id, await runner.read_file(key))
    except FileNotFoundError:
        return None
    if parsed is None:
        _log.warning("unreadable revision round %s", key)
        return None
    _round_cache[(project_id, key)] = parsed
    while len(_round_cache) > _ROUND_CACHE_MAX:
        _round_cache.popitem(last=False)
    return parsed


async def load_rounds(runner, project_id: str) -> list[Round]:
    """Every round of the project, newest first. A round that does not parse is skipped, not fatal —
    one bad record must not take the comparison away from every document."""
    ids = [k[len(ROUNDS_PREFIX):-len(".json")]
           for k in await runner.list_files(f"{ROUNDS_PREFIX}*.json")]
    loaded = await asyncio.gather(*(_load_round(runner, project_id, i) for i in ids))
    rounds = [r for r in loaded if r is not None]
    rounds.sort(key=lambda r: r.id, reverse=True)
    return rounds


def baselines_for(rounds: list[Round], path: str, current_hash: str) -> list[Round]:
    """The rounds worth comparing `path` against, newest first.

    A round whose copy equals the current text shows nothing, so it is left out. Of consecutive rounds
    holding the same copy only the newest stays: the comparison would be identical, and the newest
    names the latest point the user saw that text. A round where the document did not exist yet stays
    too (its hash is absent) — the screen says "written after these answers" instead of painting the
    whole document as added.
    """
    picked: list[Round] = []
    previous: str | None = None
    for i, r in enumerate(rounds):
        digest = r.documents.get(path, "")
        if digest == current_hash:
            previous = digest
            continue
        if i > 0 and digest == previous:
            continue
        picked.append(r)
        previous = digest
    return picked


def is_round_id(value: str) -> bool:
    return bool(_ROUND_ID.match(value))


async def baseline_text(runner, project_id: str, round_id: str, path: str) -> str | None:
    """The copy of `path` in that round. None if the document did not exist then;
    FileNotFoundError if there is no such round."""
    found = await _load_round(runner, project_id, round_id)
    if found is None:
        raise FileNotFoundError(round_id)
    digest = found.documents.get(path)
    if digest is None:
        return None
    return await runner.read_file(_blob_key(digest))
