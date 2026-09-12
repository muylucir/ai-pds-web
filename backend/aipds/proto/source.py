# backend/aipds/proto/source.py — 한 프로토타입의 **소스**를 모으는 한 가지 방법.
#
# 두 zip이 같은 질문을 한다: 개발팀 핸드오프 아카이브
# (routes/prototypes.py의 download_prototype_archive)와 프로젝트 번들
# (project_export.py). 둘 다 "이 프로토타입의 코드를 주세요"이고, 답이 두 벌이면
# 한쪽만 고쳐진다 — 그리고 이 답에는 자격증명 제외가 들어 있다
# (project_bundle.SOURCE_EXCLUDED_FILES의 `.proto-token`).
#
# **로컬 우선, S3 폴백.** 인프로세스 빌더가 로컬 빌드 트리에 직접 쓰고 ProtoHost가
# 그 자리에서 서빙하므로 정본은 디스크다. `prototypes/{slug}/bundle/`은 삭제된
# MicroVM 시절의 백업이고 지금 아무도 쓰지 않지만, 재배포로 디스크가 날아간 박스에는
# 그것만 남아 있을 수 있어 폴백으로 남긴다.
from __future__ import annotations

from pathlib import Path

from aipds.project_bundle import source_excluded
from aipds.s3store import S3StoreLike


def bundle_prefix(slug: str) -> str:
    """구 MicroVM 백업의 위치(프로젝트 상대). 폴백 전용."""
    return f"prototypes/{slug}/bundle/"


async def source_entries(*, build_dir: Path, s3: S3StoreLike,
                         slug: str) -> list[tuple[str, bytes]]:
    """(빌드 트리 상대 경로, 바이트) 목록. 소스가 없으면 빈 목록.

    바이트로 돌려주는 것이 요점이다 — 텍스트로 디코드하면 이미지와 폰트가
    U+FFFD로 망가진다(s3store.py의 get_bytes/put_bytes가 존재하는 이유).
    """
    if build_dir.is_dir():
        entries: list[tuple[str, bytes]] = []
        for path in sorted(build_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(build_dir).as_posix()
            if source_excluded(rel):
                continue
            entries.append((rel, path.read_bytes()))
        if entries:
            return entries

    prefix = bundle_prefix(slug)
    entries = []
    for key in await s3.list(prefix):
        rel = key[len(prefix):]
        if source_excluded(rel):
            continue
        entries.append((rel, await s3.get_bytes(key)))
    return entries
