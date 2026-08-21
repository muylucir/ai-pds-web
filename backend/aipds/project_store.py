"""S3 persistence for the project list (spec 2026-07-20-project-persistence-delete).

The manifest lives under the same prefix as the project's data (projects/<pid>/) so that
deletion is atomic in one prefix. 'root' is an S3StoreLike whose prefix is projects/."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MANIFEST = re.compile(r"^([^/]+)/project\.json$")


async def write_manifest(root: S3StoreLike, project_id: str, name: str | None,
                         created_at: str | None = None,
                         model_id: str | None = None,
                         language: str | None = None) -> str:
    """Write the manifest and return the created_at it recorded -- the caller (the creation
    route) registers the same instant in the registry so the list's sort key agrees.

    model_id is **copied** rather than referenced from the catalogue: this project has to keep
    running on the same model even if an administrator removes it from the catalogue. Unspecified
    is recorded as an explicit null -- omitting the key would make 'an old manifest'
    indistinguishable from 'a new project that chose no model'.

    language ("ko"|"en") is this project's **output language** -- which language its documents,
    prototypes and chat come out in. It is separate from the UI language (which is a per-user
    cookie) and is decided once at creation: changing it mid-flight would leave the aiplc-docs/**
    and transcripts already produced in the previous language, mixing document languages within
    one project. Unspecified is recorded as an explicit null for the same reason as model_id.
    """
    ts = created_at or datetime.now(timezone.utc).isoformat()
    body = json.dumps(
        {"project_id": project_id, "name": name, "created_at": ts,
         "model_id": model_id, "language": language},
        ensure_ascii=False)
    await root.put(f"{project_id}/project.json", body)
    return ts


async def restore_projects(
    root: S3StoreLike,
) -> list[tuple[str, str | None, str | None, str | None, str | None]]:
    """Scan projects/ -> GET the manifests in parallel ->
    [(pid, name, created_at, model_id, language)].
    A corrupted entry is logged and skipped -- one rotten entry does not block restoring the
    rest. created_at, model_id and language may be absent from an old manifest and so are
    nullable (sorted first, the model falls back to env, the language falls back to 'ko' --
    ProjectRegistry.get_language settles it)."""
    keys = [k for k in await root.list("") if _MANIFEST.match(k)]
    bodies = await asyncio.gather(*(root.get(k) for k in keys), return_exceptions=True)
    out: list[tuple[str, str | None, str | None, str | None, str | None]] = []
    for key, body in zip(keys, bodies):
        if isinstance(body, BaseException):
            _log.warning("manifest read failed for %s: %r", key, body)
            continue
        try:
            d = json.loads(body)
            if not isinstance(d, dict):
                _log.warning("corrupt manifest skipped: %s", key)
                continue
            pid = d.get("project_id") or _MANIFEST.match(key).group(1)  # type: ignore[union-attr]
            out.append((pid, d.get("name"), d.get("created_at"),
                        d.get("model_id"), d.get("language")))
        except (json.JSONDecodeError, TypeError):
            _log.warning("corrupt manifest skipped: %s", key)
    return out


async def delete_project_data(sessions: S3StoreLike, root: S3StoreLike,
                              project_id: str) -> None:
    """Delete the sessions and the artifacts (including the manifest) in full. Exceptions
    propagate -- the caller (the route) turns them into a 500 and keeps the registry so a retry
    is possible."""
    await sessions.delete_prefix(f"session_{project_id}/")
    await root.delete_prefix(f"{project_id}/")
