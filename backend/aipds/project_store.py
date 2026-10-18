"""S3 persistence of the project list (spec 2026-07-20-project-persistence-delete).

The manifest lives under the same prefix as the project data (projects/<pid>/) -- so
deletion is atomic over a single prefix. 'root' is an S3StoreLike whose prefix is projects/."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MANIFEST = re.compile(r"^([^/]+)/project\.json$")


async def write_manifest(root: S3StoreLike, project_id: str, name: str | None,
                         created_at: str | None = None,
                         model_id: str | None = None,
                         language: str | None = None,
                         effort: str | None = None,
                         owner: str | None = None,
                         members: tuple[str, ...] = ()) -> str:
    """Write the manifest and return the recorded created_at -- the caller (the create
    route) registers the same time in the registry so list ordering stays consistent.

    model_id is **copied** rather than referenced from the catalog: even if an admin
    removes that model from the catalog, this project must keep running on the same
    model. Unspecified is recorded as an explicit null -- omitting the key would make
    an 'old manifest' indistinguishable from 'a new project that picked no model'.

    language ("ko"|"en") is this project's **output language** -- which language the
    documents, prototypes, and chat come out in. It is separate from the UI language
    (a per-user cookie) and decided once at creation: changing it midway would leave
    the already-produced aiplc-docs/** and transcript in the previous language, mixing
    document languages within one project. For the same reason as model_id,
    unspecified is also recorded as an explicit null.

    effort is **copied** from the catalog entry's effort at creation time, together
    with model_id -- even if an admin changes the catalog's effort, an in-progress
    project's speed and output volume do not change midway. null = CLI default (true
    of every project predating this field).

    owner and members are the people with access to this project (project_access.py).
    A manifest without an owner is read as owned by the seed admin -- the registry
    makes that interpretation in one place.
    """
    ts = created_at or datetime.now(timezone.utc).isoformat()
    body = json.dumps(
        {"project_id": project_id, "name": name, "created_at": ts,
         "model_id": model_id, "language": language, "effort": effort,
         "owner": owner, "members": list(members)},
        ensure_ascii=False)
    await root.put(f"{project_id}/project.json", body)
    return ts


async def write_manifest_access(root: S3StoreLike, project_id: str, owner: str,
                                members: tuple[str, ...]) -> None:
    """Change only the manifest's owner and members.

    Read-modify-write: other fields are left exactly as read -- reassembling from the
    registry would erase keys the registry does not know (fields added later).
    Concurrent changes are serialized by the caller with project_access.access_lock.
    If the manifest is missing or unreadable, the exception propagates -- we do not
    fabricate a new manifest lacking the owner record.
    """
    key = f"{project_id}/project.json"
    d = json.loads(await root.get(key))
    if not isinstance(d, dict):
        raise ValueError(f"corrupt manifest: {key}")
    d["owner"] = owner
    d["members"] = list(members)
    await root.put(key, json.dumps(d, ensure_ascii=False))


@dataclass(frozen=True)
class RestoredProject:
    """What was read from one manifest. Fields missing from an old manifest are None (or an
    empty tuple); the registry decides the fallbacks (language 'ko', model from env, owner
    the seed admin)."""
    project_id: str
    name: str | None
    created_at: str | None
    model_id: str | None
    language: str | None
    effort: str | None
    owner: str | None
    members: tuple[str, ...]


async def restore_projects(root: S3StoreLike) -> list[RestoredProject]:
    """Scan projects/ → parallel GET of manifests → [RestoredProject].
    Corrupt entries are logged and skipped -- one rotten entry does not block restoring the rest.
    created_at, model_id, language, and owner may be absent from old manifests, so None is
    allowed (sorted first, model falls back to env, language falls back to 'ko', owner is
    the seed admin -- ProjectRegistry settles them)."""
    keys = [k for k in await root.list("") if _MANIFEST.match(k)]
    bodies = await asyncio.gather(*(root.get(k) for k in keys), return_exceptions=True)
    out: list[RestoredProject] = []
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
            owner = d.get("owner")
            members = d.get("members")
            out.append(RestoredProject(
                project_id=pid, name=d.get("name"), created_at=d.get("created_at"),
                model_id=d.get("model_id"), language=d.get("language"),
                effort=d.get("effort"),
                owner=owner if isinstance(owner, str) and owner else None,
                members=tuple(m for m in members if isinstance(m, str) and m)
                if isinstance(members, list) else ()))
        except (json.JSONDecodeError, TypeError):
            _log.warning("corrupt manifest skipped: %s", key)
    return out


async def delete_project_data(sessions: S3StoreLike, root: S3StoreLike,
                              project_id: str) -> None:
    """Delete the session + all artifacts (including the manifest). Exceptions propagate -- the
    caller (the route) turns them into 500 and keeps the registry so a retry is possible."""
    await sessions.delete_prefix(f"session_{project_id}/")
    await root.delete_prefix(f"{project_id}/")
