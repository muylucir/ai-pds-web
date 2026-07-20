"""프로젝트 목록의 S3 영속화 (스펙 2026-07-20-project-persistence-delete).

매니페스트는 프로젝트 데이터와 같은 prefix(projects/<pid>/)에 산다 — 삭제가
prefix 하나로 원자적이 되도록. 'root'는 prefix가 projects/ 인 S3StoreLike."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from pathfinder.sandbox.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MANIFEST = re.compile(r"^([^/]+)/project\.json$")


async def write_manifest(root: S3StoreLike, project_id: str, name: str | None) -> None:
    body = json.dumps(
        {"project_id": project_id, "name": name,
         "created_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False)
    await root.put(f"{project_id}/project.json", body)


async def restore_projects(root: S3StoreLike) -> list[tuple[str, str | None]]:
    """projects/ 스캔 → 매니페스트 병렬 GET → [(pid, name)]. 손상 항목은 로그
    후 건너뜀 — 하나가 썩어도 나머지 복원을 막지 않는다."""
    keys = [k for k in await root.list("") if _MANIFEST.match(k)]
    bodies = await asyncio.gather(*(root.get(k) for k in keys), return_exceptions=True)
    out: list[tuple[str, str | None]] = []
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
            out.append((pid, d.get("name")))
        except (json.JSONDecodeError, TypeError):
            _log.warning("corrupt manifest skipped: %s", key)
    return out


async def delete_project_data(sessions: S3StoreLike, root: S3StoreLike,
                              project_id: str) -> None:
    """세션 + 산출물(매니페스트 포함) 전량 삭제. 예외는 전파 — 호출부(라우트)가
    500으로 변환하고 레지스트리를 유지해 재시도를 가능하게 한다."""
    await sessions.delete_prefix(f"session_{project_id}/")
    await root.delete_prefix(f"{project_id}/")
