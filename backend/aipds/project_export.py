# backend/aipds/project_export.py — one project into one bundle zip.
#
# The format is defined by project_bundle.py. This module is the IO that **fills** that format:
# the whole S3 project prefix + the local prototype build trees → zip.
#
# **Why write to a temporary file.** The two existing zip endpoints (artifact archive, prototype
# handoff) assemble into `io.BytesIO`, but those are a few documents or one prototype's
# source. A whole project combines hundreds of transcript batches, uploads, and every prototype's
# source, and holding that in memory would compete on the workshop box for the same process space as
# prototype builds and hosting. Writing to a temporary file and streaming makes the disk
# the limit.
from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path

from aipds.agent.session_store import project_transcript_prefix
from aipds.project_bundle import (
    MANIFEST_NAME,
    build_manifest,
    proto_source_path,
    s3_key_to_bundle_path,
)
from aipds.proto import layout as proto_layout
from aipds.proto.source import source_entries
from aipds.s3store import S3StoreLike
from aipds.survey.store import survey_summary

_log = logging.getLogger(__name__)

#: Number of objects read in parallel and compressed at once. Reading sequentially adds batch count × S3 round trip
#: straight onto the wall clock (load_transcript is parallel for the same reason), while reading everything at once
#: defeats the point of writing to a temporary file — it has to be somewhere in between.
_FETCH_BATCH = 32


async def write_bundle(dest: Path, *, project_id: str, s3: S3StoreLike,
                       proto_root: Path, project: dict,
                       exported_at: str) -> dict:
    """Writes the bundle to `dest` and returns the manifest dict (for logs and response headers).

    `project` is this project's metadata (name/created_at/model_id/language), owned by the
    registry — the S3 manifest is not re-read here. What the list screen showed and what the
    bundle says must not diverge, and the truth for that is the registry.
    """
    transcript_prefix = project_transcript_prefix(project_id)
    keys = await s3.list("")
    planned = [(key, path) for key in keys
               if (path := s3_key_to_bundle_path(
                   key, transcript_prefix=transcript_prefix)) is not None]
    skipped = len(keys) - len(planned)

    slugs = sorted(proto_layout.discover(
        await s3.list(proto_layout.DISCOVERY_PREFIX)))

    counts = {"objects": len(planned), "objects_excluded": skipped}
    prototypes: list[dict] = []

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for start in range(0, len(planned), _FETCH_BATCH):
            batch = planned[start:start + _FETCH_BATCH]
            bodies = await asyncio.gather(*(s3.get_bytes(key) for key, _ in batch))
            await asyncio.to_thread(
                _write_entries, zf,
                [(path, body) for (_, path), body in zip(batch, bodies)])

        for slug in slugs:
            entries = await source_entries(
                build_dir=proto_root / project_id / slug, s3=s3, slug=slug)
            await asyncio.to_thread(
                _write_entries, zf,
                [(proto_source_path(slug, rel), data) for rel, data in entries])
            survey = await survey_summary(s3, slug)
            prototypes.append({
                "slug": slug,
                "source_files": len(entries),
                "survey": {"exists": survey.exists, "responses": survey.responses},
            })

        manifest = build_manifest(
            exported_at=exported_at, source_project_id=project_id,
            project=project, counts=counts, prototypes=prototypes)
        await asyncio.to_thread(zf.writestr, MANIFEST_NAME, manifest)

    _log.info("exported project %s: %d objects (%d excluded), %d prototype(s)",
              project_id, len(planned), skipped, len(prototypes))
    return {"counts": counts, "prototypes": prototypes}


def _write_entries(zf: zipfile.ZipFile,
                   entries: list[tuple[str, bytes]]) -> None:
    """Compression uses CPU — running it on the event loop would stall other projects' turn streams
    for that time. So callers invoke it via `asyncio.to_thread`."""
    for path, data in entries:
        zf.writestr(path, data)
