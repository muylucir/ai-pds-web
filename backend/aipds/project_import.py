# backend/aipds/project_import.py — one bundle zip into a live project.
#
# project_bundle.py defines the format, and this module is the IO that **unpacks** it.
# Registering the project and writing the manifest are not done by this module — the route does them.
# That order (content first, project.json last, registration after that) defines this feature's
# failure modes, so it must be readable in one place, and that place is the side holding the HTTP contract.
#
# **Rollback deletes only the keys we wrote.** Deleting all of `projects/{pid}/` would be
# shorter (delete_project_data does that), but it would rely on the premise "this prefix is
# ours". That premise can break between the route's 409 gate and the actual writes: if someone
# creates a project with the same id in between (the create route only checks registration, and
# the import has not registered yet), the rollback deletes someone else's project. Remembering
# what was written eliminates that race.
from __future__ import annotations

import asyncio
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from aipds.agent.session_store import project_transcript_prefix
from aipds.pathsafe import reject_unsafe_segment, write_beneath
from aipds.project_bundle import (
    MANIFEST_NAME,
    MAX_ENTRIES,
    MAX_ENTRY_BYTES,
    MAX_TOTAL_BYTES,
    BundleError,
    bundle_path_to_s3_key,
    parse_proto_source_path,
    safe_entry_name,
)
from aipds.proto.store import PrototypeStore, source_prefix
from aipds.s3store import S3StoreLike
from aipds.survey.store import TOKEN_INDEX_PREFIX, new_token

_log = logging.getLogger(__name__)

#: Number of entries unpacked and uploaded at once. Same size as project_export._FETCH_BATCH for the
#: same reason — collecting all of them makes memory the limit, and uploading one by one makes S3 round trips the wall clock.
_WRITE_BATCH = 32

#: Warning codes. Same discipline as `error_codes.py` — the backend does not know the UI language, so it
#: sends codes instead of building messages (the frontend dictionary owns the messages).
WARN_MODEL_UNAVAILABLE = "model_unavailable"
WARN_SURVEY_TOKENS_REISSUED = "survey_tokens_reissued"  # nosec B105
WARN_UNKNOWN_ENTRIES = "unknown_entries"


@dataclass
class ImportedData:
    """What the import placed, and how to undo it.

    Why `discard()` is carried in the result: the import still has one step left after this
    module finishes — the route writes `project.json` and registers. When that step fails,
    what must be undone is **what was written here**, so this place must also know how to undo it.
    Having the route receive a key list and delete on its own would split that rule into two places.
    """

    counts: dict
    warnings: list[dict]
    _keys: list[str]
    _paths: list[Path]
    _tokens: list[str]
    _s3: S3StoreLike
    _surveys_root: S3StoreLike
    _stop_at: Path

    async def discard(self) -> None:
        await _discard(self._keys, self._paths, self._tokens, s3=self._s3,
                       surveys_root=self._surveys_root, stop_at=self._stop_at)


async def apply_bundle(bundle: Path, *, project_id: str, s3: S3StoreLike,
                       surveys_root: S3StoreLike,
                       proto_root: Path) -> ImportedData:
    """Place the bundle's **contents** at the target project's location.

    Whatever step fails, it undoes what it wrote and re-raises the exception — because a
    half-imported project is indistinguishable from a normal one on screen.
    """
    transcript_prefix = project_transcript_prefix(project_id)
    written_keys: list[str] = []
    written_paths: list[Path] = []
    registered_tokens: list[str] = []

    try:
        with zipfile.ZipFile(bundle) as zf:
            plan = _plan(zf, transcript_prefix=transcript_prefix)
            counts = {
                "objects": len(plan.s3),
                "source_files": len(plan.source),
                "prototypes": len({slug for slug, _, _ in plan.source}),
            }
            await _write_s3(zf, plan.s3, s3=s3, written=written_keys)
            await _write_source(zf, plan.source, project_id=project_id,
                                proto_root=proto_root, written=written_paths)
            # Also upload the imported source as an S3 generation — S3 is the source of truth (proto/store.py).
            # Keeping it only locally would make the imported project lose its source at the next instance
            # replacement. Generation keys go into `written_keys` too, so they are rolled back together on failure.
            store = PrototypeStore(s3, project_id=project_id)
            for slug in sorted({slug for slug, _, _ in plan.source}):
                await store.snapshot(slug, proto_root / project_id / slug, "import")
                written_keys.extend(await s3.list(source_prefix(slug)))
            reissued = await _reissue_survey_tokens(
                written_keys, project_id=project_id, s3=s3,
                surveys_root=surveys_root, registered=registered_tokens)
    except BaseException:
        await _discard(written_keys, written_paths, registered_tokens,
                       s3=s3, surveys_root=surveys_root, stop_at=proto_root)
        raise

    warnings: list[dict] = []
    if reissued:
        warnings.append({"code": WARN_SURVEY_TOKENS_REISSUED, "count": reissued})
    if plan.unknown:
        warnings.append({"code": WARN_UNKNOWN_ENTRIES, "count": plan.unknown})
    _log.info("imported project %s: %d objects, %d source file(s), "
              "%d survey token(s) reissued",
              project_id, counts["objects"], counts["source_files"], reissued)
    return ImportedData(counts=counts, warnings=warnings, _keys=written_keys,
                        _paths=written_paths, _tokens=registered_tokens,
                        _s3=s3, _surveys_root=surveys_root, _stop_at=proto_root)


# ---- Plan ----


class _Plan:
    """Entries split by destination. The point is to validate everything before writing —
    discovering a dangerous entry after writing half of them increases what must be undone."""

    def __init__(self) -> None:
        #: (entry name, S3 key)
        self.s3: list[tuple[str, str]] = []
        #: (slug, entry name, path relative to the build tree)
        self.source: list[tuple[str, str, str]] = []
        self.unknown = 0


def _plan(zf: zipfile.ZipFile, *, transcript_prefix: str) -> _Plan:
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ENTRIES:
        raise BundleError(f"bundle has too many entries ({len(infos)})")
    total = 0
    plan = _Plan()
    for info in infos:
        if info.file_size > MAX_ENTRY_BYTES:
            raise BundleError(f"bundle entry too large: {info.filename}")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise BundleError("bundle expands beyond the size limit")
        name = info.filename
        if name == MANIFEST_NAME:
            continue
        safe_entry_name(name)
        key = bundle_path_to_s3_key(name, transcript_prefix=transcript_prefix)
        if key is not None:
            plan.s3.append((name, key))
            continue
        parsed = parse_proto_source_path(name)
        if parsed is not None:
            slug, rel = parsed
            # The slug becomes a local directory name. `safe_entry_name` blocked escapes across
            # the whole path, but separately from that, if a single slug is `.`,
            # `{root}/{pid}/{slug}` collapses to `{root}/{pid}`, and the cleanup after it
            # deletes sibling prototypes as well (see pathsafe.reject_unsafe_segment).
            try:
                reject_unsafe_segment(slug)
            except ValueError as e:
                raise BundleError(str(e)) from e
            plan.source.append((slug, name, rel))
            continue
        # An entry in a place we do not know. It passed the manifest gate, so this zip is
        # ours (kind + schema_version), and therefore skipping is better than rejecting —
        # things like `__MACOSX/` added when a user re-compresses the zip are common. But we do
        # not skip **silently**: the count is raised as a warning so the screen says so.
        _log.warning("skipping unrecognised bundle entry: %s", name)
        plan.unknown += 1
    return plan


# ---- Write ----


async def _write_s3(zf: zipfile.ZipFile, entries: list[tuple[str, str]], *,
                    s3: S3StoreLike, written: list[str]) -> None:
    for start in range(0, len(entries), _WRITE_BATCH):
        batch = entries[start:start + _WRITE_BATCH]
        bodies = await asyncio.to_thread(
            lambda b=batch: [zf.read(name) for name, _ in b])
        await asyncio.gather(*(s3.put_bytes(key, body)
                              for (_, key), body in zip(batch, bodies)))
        written.extend(key for _, key in batch)


async def _write_source(zf: zipfile.ZipFile, entries: list[tuple[str, str, str]],
                        *, project_id: str, proto_root: Path,
                        written: list[Path]) -> None:
    """Place prototype sources in the local build tree.

    If `{proto_root}/{pid}/{slug}/prototype/` is not empty, the card immediately shows as
    `built` (proto/session.has_build_output) and the hosting button runs the npm lifecycle.
    node_modules is not in the bundle, so that install fills it in.
    """
    def _write(batch: list[tuple[str, str, str]]) -> list[Path]:
        out: list[Path] = []
        for slug, name, rel in batch:
            # Leftovers of the same pid (a failed import, a deleted project) may contain links the
            # agent left behind — do not follow them (pathsafe).
            write_beneath(proto_root, f"{project_id}/{slug}/{rel}", zf.read(name))
            out.append(proto_root / project_id / slug / rel)
        return out

    for start in range(0, len(entries), _WRITE_BATCH):
        batch = entries[start:start + _WRITE_BATCH]
        written.extend(await asyncio.to_thread(_write, batch))


# ---- Survey tokens ----

_LIVE_QUESTIONNAIRE = "/survey/questionnaire.json"
_ARCHIVED_QUESTIONNAIRE = "/questionnaire.json"


async def _reissue_survey_tokens(keys: list[str], *, project_id: str,
                                 s3: S3StoreLike, surveys_root: S3StoreLike,
                                 registered: list[str]) -> int:
    """Rewrite the survey definition's `token` and `project_id`, and register the index.

    **The original token cannot be reused.** `surveys/by-token/{token}.json` sits at the bucket
    **root** and is a one-way index pointing to `{project_id, slug}`. Importing within the same
    bucket (project duplication does this) overwrites the same key as the original, so **the
    already-distributed original survey link moves over to the new project** — responses pile up
    in the wrong project, and that fact is never left anywhere as an error.

    Archived rounds also get new token values but **their index is not registered**. There is no
    reason for past rounds' links to stay alive, and keeping the original token would make it the
    same value as the original instance's link. `SurveyStore.purge`'s `_collect_tokens` tries to
    delete indexes of unregistered tokens, but `delete_prefix` on a missing key returns 0, so it
    is harmless, and what that side requires (being able to **read** the token from the definition) is preserved.
    """
    reissued = 0
    for key in keys:
        live = key.endswith(_LIVE_QUESTIONNAIRE)
        if not live and not (key.endswith(_ARCHIVED_QUESTIONNAIRE)
                             and "/survey/archive/" in key):
            continue
        try:
            data = json.loads(await s3.get(key))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # If the definition cannot be read, the token cannot be changed either. Leaving it as is
            # would keep the original's token alive, so the whole import is failed (the caller rolls back).
            raise BundleError(f"unreadable questionnaire in bundle: {key}") from e
        if not isinstance(data, dict):
            raise BundleError(f"unreadable questionnaire in bundle: {key}")
        token = new_token()
        data["token"] = token
        data["project_id"] = project_id
        slug = data.get("slug")
        await s3.put(key, json.dumps(data, ensure_ascii=False))
        reissued += 1
        if live and isinstance(slug, str) and slug:
            # Write the index **after** the definition. save_questionnaire uses the opposite order
            # (index first) to avoid the unrecoverable state of "a definition with no index". Here,
            # rollback on failure deletes both, so that risk does not exist; instead, what matters
            # is writing only after it is on the rollback list.
            await surveys_root.put(
                f"{TOKEN_INDEX_PREFIX}{token}.json",
                json.dumps({"project_id": project_id, "slug": slug}))
            registered.append(token)
    return reissued


# ---- Rollback ----


async def _discard(keys: list[str], paths: list[Path], tokens: list[str], *,
                   s3: S3StoreLike, surveys_root: S3StoreLike,
                   stop_at: Path) -> None:
    """Delete what a failed import wrote. Best effort — raising here would mask the
    original cause of the failure."""
    for token in tokens:
        try:
            await surveys_root.delete_prefix(f"{TOKEN_INDEX_PREFIX}{token}.json")
        except Exception:
            _log.warning("could not roll back survey token index", exc_info=True)
    # Passing a full key to `delete_prefix` is the established convention for single-key deletion
    # (S3StoreLike has no single delete — survey/store.py's purge has the same shape).
    results = await asyncio.gather(*(s3.delete_prefix(key) for key in keys),
                                   return_exceptions=True)
    failed = sum(1 for r in results if isinstance(r, BaseException))
    if failed:
        _log.warning("import rollback left %d/%d object(s) behind",
                     failed, len(keys))
    _discard_paths(paths, stop_at=stop_at)


def _discard_paths(paths: list[Path], *, stop_at: Path) -> None:
    """Delete the written files and sweep upward the directories that became empty because of it.

    `{proto_root}/{pid}` is not rmtree'd wholesale for the same reason as on the S3 side: there
    may be leftovers from an earlier failed import with this pid or a deleted project, and
    deleting those too is outside the scope this function promised.

    `stop_at` is the boundary of the upward walk (the prototype root). Without it, this loop keeps
    climbing to the parent when the root becomes empty — cleanup that starts deleting parent
    directories is not cleanup.
    """
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _log.warning("could not roll back %s", path, exc_info=True)
    for directory in sorted({p.parent for p in paths},
                            key=lambda d: len(d.parts), reverse=True):
        current = directory
        while current != stop_at and stop_at in current.parents:
            try:
                current.rmdir()
            except OSError:
                break  # not empty or missing — either way, stop here
            current = current.parent
