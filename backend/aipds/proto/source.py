# backend/aipds/proto/source.py — 한 프로토타입의 **소스**를 모으는 한 가지 방법.
#
# 두 zip이 같은 질문을 한다: 개발팀 핸드오프 아카이브
# (routes/prototypes.py의 download_prototype_archive)와 프로젝트 번들
# (project_export.py). 둘 다 "이 프로토타입의 코드를 주세요"이고, 답이 두 벌이면
# 한쪽만 고쳐진다 — 그리고 이 답에는 자격증명 제외가 들어 있다
# (project_bundle.SOURCE_EXCLUDED_FILES의 `.proto-token`).
#
# **로컬 우선, S3 폴백.** 인프로세스 빌더가 로컬 빌드 트리에 직접 쓰고 ProtoHost가
# 그 자리에서 서빙하므로 가장 새로운 것은 디스크다. 디스크에 없으면(교체된 인스턴스)
# S3의 현재 소스 세대가 정본이다(proto/store.py).
from __future__ import annotations

from pathlib import Path

import os

from aipds.project_bundle import SOURCE_EXCLUDED_DIRS, source_excluded
from aipds.proto.store import PrototypeStore, local_entries
from aipds.s3store import S3StoreLike


async def source_entries(*, build_dir: Path, s3: S3StoreLike,
                         slug: str) -> list[tuple[str, bytes]]:
    """(빌드 트리 상대 경로, 바이트) 목록. 소스가 없으면 빈 목록.

    바이트로 돌려주는 것이 요점이다 — 텍스트로 디코드하면 이미지와 폰트가
    U+FFFD로 망가진다(s3store.py의 get_bytes/put_bytes가 존재하는 이유).
    """
    entries = local_entries(build_dir)
    if entries:
        return entries
    return [(rel, data) for rel, data in await PrototypeStore(s3).entries(slug)
            if not source_excluded(rel)]


def newest_source_mtime(build_dir: Path) -> float | None:
    """빌드 트리에서 **소스**의 가장 최근 수정 시각(epoch 초). 소스가 없으면 None.

    `ProtoHost`의 `built_at`과 비교해 "떠 있는 서버가 이전 버전인가"를 판정한다.
    실행 중인 프로토타입을 수정할 수 있게 되면서 서버는 그대로 뜬 채 소스만 바뀌는
    구간이 생겼고, 그때 카드가 "실행 :4007"만 말하면 사용자는 수정이 반영됐다고
    읽는다 — 참가자에게 나간 링크가 보여 주는 것은 이전 버전이다.

    **제외 규칙이 정답의 일부다.** 그냥 최신 mtime을 쓰면 모든 프로토타입이 영원히
    낡은 것으로 나온다: `npm install`이 `node_modules/`를, `npm run build`가
    `.next/`를, 호스팅이 `.proto-host.log`를 `built_at` **뒤에** 쓴다. 그래서 이
    함수가 `source_entries`와 같은 집합을 쓴다 — 그것이 이 모듈이 그 집합의
    소비자인 이유이고, 두 벌로 두면 한쪽만 고쳐진다.

    **디렉토리는 걸어 들어가지 않고 잘라낸다.** 목록 라우트가 카드마다 이것을 부르고
    그 목록은 폴링된다. `node_modules`는 실측 수만 개 파일이라, 걸으면서 필터하면
    정답은 같지만 목록 응답이 느려진다 — 그래서 `rglob`(`source_entries`가 쓰는
    형태)이 아니라 `os.walk`로 `dirnames`를 잘라낸다.
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
                # 걷는 사이에 사라진 파일(에이전트가 지금 쓰고 있다). 판정은
                # 부수 정보이므로 그것 때문에 목록 조회가 죽으면 안 된다.
                continue
            if newest is None or mtime > newest:
                newest = mtime
    return newest
