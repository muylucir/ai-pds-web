"""프로젝트 목록의 S3 영속화 (스펙 2026-07-20-project-persistence-delete).

매니페스트는 프로젝트 데이터와 같은 prefix(projects/<pid>/)에 산다 — 삭제가
prefix 하나로 원자적이 되도록. 'root'는 prefix가 projects/ 인 S3StoreLike."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from pathfinder.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MANIFEST = re.compile(r"^([^/]+)/project\.json$")


async def write_manifest(root: S3StoreLike, project_id: str, name: str | None,
                         created_at: str | None = None,
                         model_id: str | None = None,
                         language: str | None = None) -> str:
    """매니페스트를 쓰고 기록된 created_at을 반환한다 — 호출부(생성 라우트)가
    같은 시각을 레지스트리에도 등록해 목록 정렬 기준을 일치시킨다.

    model_id는 카탈로그를 참조하지 않고 **복사**한다: 관리자가 그 모델을
    카탈로그에서 지워도 이 프로젝트는 계속 같은 모델로 돌아야 한다. 미지정은
    명시적 null로 기록한다 — 키를 빼면 '구 매니페스트'와 '모델을 고르지 않은
    새 프로젝트'를 구별할 수 없다.

    language("ko"|"en")는 이 프로젝트의 **생성물 언어**다 — 문서·프로토타입·
    채팅이 어느 언어로 나오는지. UI 언어와 별개이고(그쪽은 사용자별 쿠키),
    생성 시점 1회 결정이다: 진행 중에 바꾸면 이미 만들어진 aiplc-docs/**와
    트랜스크립트가 이전 언어로 남아 한 프로젝트 안에서 문서 언어가 섞인다.
    model_id와 같은 이유로 미지정도 명시적 null로 기록한다.
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
    """projects/ 스캔 → 매니페스트 병렬 GET →
    [(pid, name, created_at, model_id, language)].
    손상 항목은 로그 후 건너뜀 — 하나가 썩어도 나머지 복원을 막지 않는다.
    created_at·model_id·language는 구 매니페스트에 없을 수 있어 None 허용
    (정렬 시 맨 앞, 모델은 env 폴백, 언어는 'ko' 폴백 —
    ProjectRegistry.get_language가 확정한다)."""
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
    """세션 + 산출물(매니페스트 포함) 전량 삭제. 예외는 전파 — 호출부(라우트)가
    500으로 변환하고 레지스트리를 유지해 재시도를 가능하게 한다."""
    await sessions.delete_prefix(f"session_{project_id}/")
    await root.delete_prefix(f"{project_id}/")
