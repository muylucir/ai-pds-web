# backend/aipds/project_export.py — 프로젝트 하나를 번들 zip 하나로.
#
# 포맷은 project_bundle.py가 정한다. 이 모듈은 그 포맷을 **채우는** IO다:
# S3 프로젝트 prefix 전체 + 로컬 프로토타입 빌드 트리 → zip.
#
# **왜 임시 파일에 쓰는가.** 기존 두 zip 엔드포인트(산출물 아카이브, 프로토타입
# 핸드오프)는 `io.BytesIO`에 조립하는데, 그쪽은 문서 몇 개이거나 한 프로토타입의
# 소스다. 프로젝트 전체는 트랜스크립트 배치 수백 개와 업로드와 모든 프로토타입의
# 소스를 합친 것이고, 그것을 메모리에 두면 워크숍 박스에서 프로토타입 빌드와
# 호스팅과 같은 프로세스 공간을 다툰다. 임시 파일에 쓰고 스트리밍하면 상한이
# 디스크가 된다.
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

#: 한 번에 병렬로 읽어 한 번에 압축하는 객체 수. 순차로 읽으면 배치 수 × S3 왕복이
#: 그대로 벽시계에 붙고(load_transcript가 같은 이유로 병렬이다), 전부 모아서 읽으면
#: 임시 파일에 쓰는 의미가 없어진다 — 그 사이 어딘가여야 한다.
_FETCH_BATCH = 32


async def write_bundle(dest: Path, *, project_id: str, s3: S3StoreLike,
                       proto_root: Path, project: dict,
                       exported_at: str) -> dict:
    """`dest`에 번들을 쓰고 매니페스트 dict를 돌려준다(로그·응답 헤더용).

    `project`는 이 프로젝트의 메타데이터(name/created_at/model_id/language)이고
    레지스트리가 소유한다 — 여기서 S3 매니페스트를 다시 읽지 않는다. 목록 화면이
    보여 준 것과 번들이 말하는 것이 어긋나면 안 되고, 그 진실은 레지스트리다.
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
    """압축은 CPU를 쓴다 — 이벤트 루프에서 돌리면 그 시간 동안 다른 프로젝트의
    턴 스트림이 멈춘다. 그래서 호출부가 `asyncio.to_thread`로 부른다."""
    for path, data in entries:
        zf.writestr(path, data)
