# backend/aipds/proto/source.py — the single way to gather one prototype's **source**.
#
# Two zips ask the same question: the developer-team handoff archive
# (download_prototype_archive in routes/prototypes.py) and the project bundle
# (project_export.py). Both ask "give me this prototype's code", and with two copies of the
# answer only one gets fixed — and this answer includes the credential exclusion
# (`.proto-token` in project_bundle.SOURCE_EXCLUDED_FILES).
#
# **Local first, S3 fallback.** The in-process builder writes directly into the local build
# tree and ProtoHost serves from that spot, so the newest copy is on disk. If it is not on
# disk (a replaced instance), the current source generation in S3 is canonical (proto/store.py).
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aipds.project_bundle import SOURCE_EXCLUDED_DIRS, source_excluded
from aipds.proto.store import PrototypeStore, local_entries
from aipds.s3store import S3StoreLike


async def source_entries(*, build_dir: Path, s3: S3StoreLike,
                         slug: str) -> list[tuple[str, bytes]]:
    """List of (build-tree relative path, bytes). Empty list if there is no source.

    Returning bytes is the point — decoding to text would corrupt images and fonts
    into U+FFFD (the reason get_bytes/put_bytes exist in s3store.py).
    """
    entries = await asyncio.to_thread(local_entries, build_dir)
    if entries:
        return entries
    return [(rel, data) for rel, data in await PrototypeStore(s3).entries(slug)
            if not source_excluded(rel)]


def newest_source_mtime(build_dir: Path) -> float | None:
    """The most recent modification time (epoch seconds) of the **source** in the build tree. None if there is no source.

    Compared against `ProtoHost`'s `built_at` to decide "is the running server an older
    version?". Once running prototypes became editable, there are periods where the server
    keeps running while only the source changes, and if the card then says only "running
    :4007", the user reads it as the change having been applied — while the link sent to
    participants shows the previous version.

    **The exclusion rules are part of the right answer.** Using the plain latest mtime would
    report every prototype as stale forever: `npm install` writes `node_modules/`,
    `npm run build` writes `.next/`, and hosting writes `.proto-host.log`, all **after**
    `built_at`. So this function uses the same set as `source_entries` — that is why this
    module is a consumer of that set; with two copies, only one gets fixed.

    **Directories are pruned rather than walked into.** The list route calls this for every
    card, and that list is polled. `node_modules` is measured at tens of thousands of files,
    so filtering while walking gives the same answer but slows the list response — hence
    pruning `dirnames` with `os.walk` (the same walk as `store.local_entries`).
    """
    if not build_dir.is_dir():
        return None
    newest: float | None = None
    for dirpath, dirnames, filenames in os.walk(build_dir):
        dirnames[:] = [d for d in dirnames if d not in SOURCE_EXCLUDED_DIRS]
        rel_dir = Path(dirpath).relative_to(build_dir)
        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if source_excluded(rel):
                continue
            try:
                mtime = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                # A file that vanished mid-walk (the agent is writing right now). This
                # check is auxiliary information, so it must not kill the list query.
                continue
            if newest is None or mtime > newest:
                newest = mtime
    return newest
