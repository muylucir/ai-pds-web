# backend/aipds/proto/store.py — the source of truth for prototypes is S3.
#
# **Why this exists.** Build conversations, session ids, handoffs, and surveys were in S3, but the
# built source tree itself, the access token, and the fact that it is "being hosted" lived only on
# the instance's local disk and in memory. So two events produced the same outcome:
#
#   - Instance replacement (HostingStack deploy, failure): the source disappears so the card goes
#     back to `none`, and the token file disappears so every link already handed out returns 404.
#     Rebuilding from the spec produces **a different thing** from the one the survey was run on.
#   - Backend restart: the token is reread from disk, but the hosting registry is in memory, so the
#     link is alive while the server it points to does not come back up.
#
# This module keeps the source of truth for those three in S3. The local tree is a cache — if it is missing, it is restored from here.
#
# Keys (relative to the project prefix):
#
#   prototypes/{slug}/source/current.json    summary of the current generation
#   prototypes/{slug}/source/{gen:06d}/{rel} files of that generation (bytes)
#   prototypes/{slug}/access-token           access token
#   prototypes/{slug}/hosting.json           desired state (whether it should be hosted)
#
# Bucket root: `surveys/preview-tokens/{sha256(token)}.json` → {project_id, slug}. The public
# preview does not know which project a request is for until it arrives, so the index must be at
# the root (same reason as the survey token index `surveys/by-token/`). Keys are hashes so that a
# listing does not reveal the plaintext tokens.
#
# **Why under `surveys/`.** The backend role's S3 permissions are a prefix allowlist
# (BACKEND_BUCKET_PREFIXES in infra/lib/backend-permissions.ts). Any other root prefix is
# AccessDenied, and widening the list requires deploying HostingStack — and that deploy can replace
# the EC2 instance. `surveys/` is already the home of "public-link token indexes at the bucket root",
# so an index of the same nature goes next to it.
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aipds.pathsafe import (read_bytes_beneath, read_text_beneath, reject_unsafe,
                            write_beneath)
from aipds.project_bundle import SOURCE_EXCLUDED_DIRS, source_excluded
from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: Number of generations kept. Only enough for rollback (a funnel task) — the source is small, but each generation is a full copy.
KEEP_GENERATIONS = 5
_UPLOAD_CONCURRENCY = 8
#: An S3 key prefix, not a credential -- the `nosec` is for the name, which is all
#: bandit B105 looks at.
TOKEN_INDEX_PREFIX = "surveys/preview-tokens/"  # nosec B105


def source_prefix(slug: str) -> str:
    return f"prototypes/{slug}/source/"


def _current_key(slug: str) -> str:
    return f"{source_prefix(slug)}current.json"


def _gen_prefix(slug: str, gen: int) -> str:
    return f"{source_prefix(slug)}{gen:06d}/"


def token_key(slug: str) -> str:
    return f"prototypes/{slug}/access-token"


def hosting_key(slug: str) -> str:
    return f"prototypes/{slug}/hosting.json"


def _index_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{TOKEN_INDEX_PREFIX}{digest}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_entries(build_dir: Path) -> list[tuple[str, bytes]]:
    """(relative path, bytes) of the build tree. The exclusion rules are the same set as the
    bundle and archives (`project_bundle.source_excluded`) — `node_modules`, `.next`, hosting
    logs, and the token are not put into a generation.

    **Excluded directories are pruned rather than walked into.** `node_modules` is measured at
    tens of thousands of files, so walking all of it and filtering afterwards gives the same answer
    but the walk alone takes seconds. `source_excluded` drops a path if any segment is an excluded
    directory, so pruning those directories gives the same answer.

    **Symbolic links are not included.** This tree is written by the build agent and the prototype,
    so following file links would put whatever is beyond the link (other projects, backend config)
    into generations, downloads, and exports. `os.walk` does not follow directory links, file links
    are skipped, and reads also go through `read_bytes_beneath` (a link swapped in after the walk
    but before the read).

    **Order is by path segment.** The generation hash (`_hash_entries`) is computed in this order,
    so switching to string sorting (which flips the order of `a.b` and `a/b`) would create a new
    generation while the source is unchanged.

    This is a sync function — it reads every file, so callers invoke it via `asyncio.to_thread`.
    """
    if not build_dir.is_dir():
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(build_dir):
        dirnames[:] = [d for d in dirnames if d not in SOURCE_EXCLUDED_DIRS]
        rel_dir = Path(dirpath).relative_to(build_dir)
        for name in filenames:
            path = Path(dirpath) / name
            rel = (rel_dir / name).as_posix()
            if source_excluded(rel) or path.is_symlink() or not path.is_file():
                continue
            found.append(rel)
    found.sort(key=lambda rel: rel.split("/"))
    out: list[tuple[str, bytes]] = []
    for rel in found:
        try:
            out.append((rel, read_bytes_beneath(build_dir, rel)))
        except OSError:
            continue  # turned into a link or disappeared during the walk
    return out


def _hash_entries(entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def has_build_output(build_dir: Path) -> bool:
    """Whether `prototype/` under the build directory has output.

    This is the single definition of "built". Three places ask this question, and all of them
    must go through here -- `first_prompt()` (what to instruct), the `build_complete`
    tool (whether to accept a completion claim), and the list route (whether to show the card as
    built). If the criteria diverge, the tool accepts completion while the list does not show it as
    built (or the reverse).

    The point is to look at `prototype/` and not at the build directory itself.
    `start()` plants the spec .md before the agent runs, and a previous hosting attempt
    can leave `.proto-host.log`/`.pid` -- the build directory existing only means a session
    started, not that anything was built.

    It checks only direct children and does not recurse: it stays cheap on every list call
    even after node_modules/.next exist.
    """
    proto_dir = build_dir / "prototype"
    try:
        return proto_dir.is_dir() and any(proto_dir.iterdir())
    except OSError:
        return False


#: Content hash of `prototype/` (`output_digest`) at the last accepted completion claim.
#: Why it lives at the build tree root: generation snapshots, restores, and project bundles move
#: the tree as a whole, so this value travels with the source — a copy kept elsewhere could diverge from it.
#: A reset deletes the tree, so it disappears too, and the first build after that can finish without the gate.
COMPLETED_MARKER = ".aipds-completed"

#: By-products that tools rewrite. Hosting runs `npm install` and the build in `prototype/`
#: (proto/host.py), and the agent's type checks rewrite these too — including them would report
#: "changed" when nobody changed the source. Work that changes dependencies shows up in `package.json`.
_DIGEST_EXCLUDED = frozenset({"package-lock.json", "tsconfig.tsbuildinfo",
                              "next-env.d.ts"})


def output_digest(build_dir: Path) -> str:
    """Content hash of `prototype/`. Same rules as the generation hash, but only over the output.

    It must not look at the whole build tree — `start()` re-plants the spec, instructions, and
    change history every time, so the hash would change even if the agent changed nothing.

    This is a sync function (same reason as `local_entries`).
    """
    return _hash_entries([(rel, data) for rel, data in local_entries(build_dir / "prototype")
                          if rel.rsplit("/", 1)[-1] not in _DIGEST_EXCLUDED])


def completed_digest(build_dir: Path) -> str | None:
    """Hash at the last completion claim, or None (first build, or a prototype from before the marker)."""
    try:
        return read_text_beneath(build_dir, COMPLETED_MARKER).strip() or None
    except (OSError, ValueError):
        return None


def mark_completed(build_dir: Path, digest: str) -> None:
    write_beneath(build_dir, COMPLETED_MARKER, digest)


class PrototypeStore:
    """Source of truth for one project's prototypes. `s3` is the project store, `root` the bucket root."""

    def __init__(self, s3: S3StoreLike, root: S3StoreLike | None = None,
                 project_id: str | None = None) -> None:
        self._s3 = s3
        self._root = root
        self._project_id = project_id

    # ---- Source generations ----

    async def current(self, slug: str) -> dict | None:
        try:
            data = json.loads(await self._s3.get(_current_key(slug)))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("gen"), int) else None

    async def has_source(self, slug: str) -> bool:
        return await self.current(slug) is not None

    async def snapshot(self, slug: str, build_dir: Path, reason: str) -> int | None:
        """Push the local build tree as a new generation. Returns the new generation number, or None.

        None when: there is no build output (turning a spec-only tree into a generation would make
        a restore falsely report "built"), or the content is identical to the current generation.

        `current.json` is written **last**. If uploading the files fails midway, the half-uploaded
        generation is not pointed to, so a restore uses the previous generation.
        """
        if not has_build_output(build_dir):
            return None
        entries = await asyncio.to_thread(local_entries, build_dir)
        digest = _hash_entries(entries)
        current = await self.current(slug)
        if current is not None and current.get("hash") == digest:
            return None
        gen = (current["gen"] + 1) if current is not None else 1
        prefix = _gen_prefix(slug, gen)
        sem = asyncio.Semaphore(_UPLOAD_CONCURRENCY)

        async def put(rel: str, data: bytes) -> None:
            async with sem:
                await self._s3.put_bytes(prefix + rel, data)

        await asyncio.gather(*(put(rel, data) for rel, data in entries))
        await self._s3.put(_current_key(slug), json.dumps({
            "gen": gen, "hash": digest, "files": len(entries),
            "reason": reason, "created_at": _now()}))
        await self._prune(slug, gen)
        _log.info("prototype source snapshot %s/%s gen=%d files=%d (%s)",
                  self._project_id, slug, gen, len(entries), reason)
        return gen

    async def _prune(self, slug: str, newest: int) -> None:
        keep_from = newest - KEEP_GENERATIONS + 1
        base = source_prefix(slug)
        gens = set()
        for key in await self._s3.list(base):
            head = key[len(base):].split("/", 1)[0]
            if head.isdigit():
                gens.add(int(head))
        for gen in sorted(g for g in gens if g < keep_from):
            await self._s3.delete_prefix(_gen_prefix(slug, gen))

    async def entries(self, slug: str) -> list[tuple[str, bytes]]:
        """(relative path, bytes) of the current generation. Empty list if there is no generation."""
        current = await self.current(slug)
        if current is None:
            return []
        prefix = _gen_prefix(slug, current["gen"])
        keys = await self._s3.list(prefix)
        bodies = await asyncio.gather(*(self._s3.get_bytes(k) for k in keys))
        return [(k[len(prefix):], b) for k, b in zip(keys, bodies)]

    async def restore(self, slug: str, build_dir: Path) -> bool:
        """Restore from the current generation if there is no local build output. True if restored.

        If there is local output it is left alone — it may be newer (edits not yet snapshotted).
        The access token is not restored here — the local copy lives in the hosting state tree, and
        hosting owns it (proto/hosting.restore_local).
        """
        restored = False
        if not has_build_output(build_dir):
            files = await self.entries(slug)
            if files:
                def write() -> None:
                    build_dir.mkdir(parents=True, exist_ok=True)
                    for rel, data in files:
                        reject_unsafe(rel)       # we wrote the keys, but check before using them as paths
                        # This tree is written by the agent — do not follow links (pathsafe).
                        write_beneath(build_dir, rel, data)
                await asyncio.to_thread(write)
                restored = True
                _log.info("restored prototype source %s/%s from S3 (%d files)",
                          self._project_id, slug, len(files))
        return restored

    # ---- Access token ----

    async def load_token(self, slug: str) -> str | None:
        try:
            token = (await self._s3.get(token_key(slug))).strip()
        except FileNotFoundError:
            return None
        return token or None

    async def save_token(self, slug: str, token: str) -> None:
        """Write the token and the root index. Skip if it is already the same value (this is
        called on every hosting restart)."""
        if await self.load_token(slug) == token:
            return
        await self._s3.put(token_key(slug), token)
        if self._root is not None and self._project_id is not None:
            await self._root.put(_index_key(token), json.dumps(
                {"project_id": self._project_id, "slug": slug}))

    async def forget_token(self, slug: str) -> None:
        """Delete the root index. Must be called **before** a reset or project deletion removes
        `prototypes/{slug}/` — the token to read is stored under it."""
        token = await self.load_token(slug)
        if token and self._root is not None:
            await self._root.delete_prefix(_index_key(token))

    # ---- Desired state ----

    async def set_desired_running(self, slug: str) -> None:
        await self._s3.put(hosting_key(slug), json.dumps(
            {"desired": "running", "since": _now()}))

    async def clear_desired(self, slug: str) -> None:
        await self._s3.delete_prefix(hosting_key(slug))

    async def desired_running(self) -> list[str]:
        slugs = []
        for key in await self._s3.list("prototypes/"):
            head, _, rest = key[len("prototypes/"):].partition("/")
            if rest == "hosting.json":
                slugs.append(head)
        return sorted(slugs)


async def resolve_token(root: S3StoreLike, token: str) -> tuple[str, str] | None:
    """Token → (project_id, slug) via the root index. None if absent."""
    try:
        data = json.loads(await root.get(_index_key(token)))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    pid, slug = data.get("project_id"), data.get("slug")
    if isinstance(pid, str) and isinstance(slug, str):
        return pid, slug
    return None
