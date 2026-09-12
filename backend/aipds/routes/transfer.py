# backend/aipds/routes/transfer.py — 프로젝트를 인스턴스 사이로 옮기는 라우트.
#
# 내보내기는 프로젝트 하나를 zip 하나로 내려주고(project_export.py), 가져오기는
# 그 zip을 다른 인스턴스에서 되살린다(project_import.py). 번들 포맷은
# project_bundle.py가 소유하고, 이 파일은 HTTP 계약만 갖는다.
#
# **업로드가 이 프로세스를 지나지 않는다.** 가져오기는 3단계다 —
# presign 요청 → 브라우저가 S3로 직접 PUT → 임포트 요청. nginx의
# `client_max_body_size`(6m, 5MB 업로드 상한에 맞춰 둔 값)를 올리지 않고 큰 번들을
# 받는 유일한 길이고, 그 값을 올리려면 EC2를 교체하는 배포가 필요하다
# (infra/lib/user-data.ts는 인스턴스 user-data이므로 변경이 곧 교체다).
from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from aipds import error_codes as ec
from aipds.pathsafe import reject_unsafe_segment
from aipds.project_export import write_bundle

_log = logging.getLogger(__name__)


def _reject_traversal_params(request: Request) -> None:
    """`{pid}`는 한 개의 평범한 경로 세그먼트여야 한다.

    routes/prototypes.py의 같은 이름 가드와 같은 이유다: 이 값이 로컬 디렉터리
    이름(`{proto_root}/{pid}/…`)과 S3 키 prefix로 들어가고, 퍼센트 인코딩된
    `%2e%2e`는 `path_params`에 이미 디코드된 채 도착해 정상 라우팅된다. 404로
    답하는 것도 그쪽과 같다 — 주소가 가리키는 것이 없다는 뜻이다.
    """
    value = request.path_params.get("pid")
    if value is None:
        return
    try:
        reject_unsafe_segment(value)
    except ValueError:
        _log.warning("rejected unsafe pid in transfer route: %r", value)
        raise HTTPException(status_code=404, detail="invalid pid")


router = APIRouter(dependencies=[Depends(_reject_traversal_params)])


def _content_disposition(pid: str, exported_at: str) -> str:
    """RFC 6266/5987. `pid`는 검증되지 않은 사용자 입력(한글 포함)일 수 있어
    raw interpolation은 latin-1 헤더 인코딩에서 UnicodeEncodeError(500)가 된다 —
    routes/artifacts.py가 같은 이유로 같은 모양을 갖는다."""
    stamp = re.sub(r"[^0-9]", "", exported_at)[:14]
    name = f"aipds-export-{pid}-{stamp}.zip"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "aipds-export.zip"
    return (f'attachment; filename="{safe}"; '
            f"filename*=UTF-8''{quote(name, safe='')}")


@router.get("/projects/{pid}/export")
async def export_project(pid: str):
    """프로젝트 전체를 번들 zip으로.

    **살아 있는 빌드 세션이 있으면 409다.** 에이전트가 그 트리에 쓰고 있는 동안
    담으면 반쯤 쓰인 소스가 들어가고, 그 zip은 정직한 스냅샷이 아니다. Discovery
    턴은 막지 않는다 — 파일 계약 모델에서는 그 순간의 S3 상태가 유효한 스냅샷이고,
    턴이 끝날 때까지 기다리게 하는 비용이 워크숍에서 더 크다.
    """
    import aipds.app as app_module
    from aipds.proto import layout as proto_layout
    from aipds.routes.prototypes import _live_session

    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")

    s3 = app_module.s3_store_factory(pid)
    for slug in proto_layout.discover(await s3.list(proto_layout.DISCOVERY_PREFIX)):
        if _live_session(pid, slug) is not None:
            raise HTTPException(status_code=409,
                                detail=ec.BUILD_SESSION_ACTIVE)

    exported_at = datetime.now(timezone.utc).isoformat()
    project = {
        "name": app_module.registry.get_name(pid),
        "created_at": app_module.registry.get_created_at(pid),
        "model_id": app_module.registry.get_model_id(pid),
        "language": app_module.registry.get_language(pid),
    }
    # delete=False + BackgroundTask: FileResponse가 본문을 다 흘린 **뒤에** 지운다.
    # `with`로 감싸면 응답을 만들기도 전에 파일이 사라진다.
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    dest = Path(tmp.name)
    try:
        await write_bundle(dest, project_id=pid, s3=s3,
                           proto_root=app_module._proto_root(),
                           project=project, exported_at=exported_at)
    except BaseException:
        _cleanup(dest)
        raise
    return FileResponse(
        dest, media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(pid, exported_at)},
        background=BackgroundTask(_cleanup, dest))


def _cleanup(path: Path) -> None:
    """임시 번들을 지운다. 실패는 로그로만 — 응답은 이미 나갔다."""
    try:
        os.unlink(path)
    except OSError:
        _log.warning("could not remove temporary bundle %s", path, exc_info=True)
