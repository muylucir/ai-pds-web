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

import asyncio
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from aipds import error_codes as ec
from aipds.import_staging import (
    CONTENT_TYPE,
    MAX_IMPORT_BYTES,
    PRESIGN_EXPIRES_IN,
    new_upload_id,
)
from aipds.pathsafe import reject_unsafe_segment
from aipds.project_bundle import MANIFEST_NAME, BundleError, parse_manifest
from aipds.project_export import write_bundle
from aipds.project_import import (
    WARN_MODEL_UNAVAILABLE,
    apply_bundle,
)
from aipds.project_store import write_manifest

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


# ---- 가져오기 ----
#
# 세 단계다: presign 요청 → 브라우저가 S3로 직접 PUT → 임포트 요청.
#
# **경로가 `/projects/…` 아래에 없는 이유.** `POST /projects/import/uploads` 는
# `POST /projects/{pid}/uploads`(참고자료 업로드)와 같은 모양이고, 어느 쪽이 이기는지는
# 라우터 등록 순서로 결정된다. 그런 정합성은 조용히 깨진다 — `import`라는 이름의
# 프로젝트를 만든 사용자는 자기 프로젝트의 업로드가 임포트 스테이징으로 가는 것을
# 보게 된다. 별개의 명사면 그 함정이 존재하지 않는다.


class ImportUploadRequest(BaseModel):
    #: 클라이언트가 알린 번들 크기. **권위 있는 값이 아니다** — 상한을 넘는 업로드가
    #: 애초에 시작되지 않게 하는 편의이고, 진짜 검사는 임포트 시점의 `head_object`다.
    size_bytes: int


class ImportRequest(BaseModel):
    upload_id: str
    #: 미지정이면 번들이 말하는 원본 id를 쓴다. 그 id가 이미 있으면 409이고,
    #: 그때 화면이 이 필드를 채워 **같은 upload_id로** 다시 부른다.
    project_id: str | None = None


#: 우리가 발급한 스테이징 id의 모양(`import_staging.new_upload_id`의 uuid4 hex).
#: 이 값이 S3 키가 되므로 모양을 강제하는 것이 traversal을 완전히 닫는다 —
#: 클라이언트가 키를 정할 수 있는 여지가 남지 않는다.
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _staging():
    """스테이징. 버킷이 없으면 503 — 조용히 강등하면 사용자가 수백 MB를 올린
    뒤에 그 사실을 알게 된다."""
    import aipds.app as app_module
    try:
        return app_module.import_staging()
    except RuntimeError:
        _log.exception("project import is unavailable")
        raise HTTPException(status_code=503, detail=ec.IMPORT_UNAVAILABLE)


@router.post("/project-imports/uploads", status_code=201)
async def create_import_upload(body: ImportUploadRequest):
    """번들을 올릴 presigned PUT URL. 업로드는 이 프로세스를 지나지 않는다."""
    if body.size_bytes <= 0 or body.size_bytes > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail=ec.EXPORT_TOO_LARGE)
    staging = _staging()
    upload_id = new_upload_id()
    url = await staging.presign_put(upload_id)
    return {"upload_id": upload_id, "url": url,
            "content_type": CONTENT_TYPE,
            "expires_in": PRESIGN_EXPIRES_IN,
            "max_bytes": MAX_IMPORT_BYTES}


@router.post("/project-imports", status_code=201)
async def import_project(body: ImportRequest):
    """스테이징된 번들을 프로젝트로 되살린다.

    **순서가 load-bearing이다.** 내용 → `project.json` → 등록. `project.json`이
    "이 프로젝트가 존재한다"의 정본이므로(`restore_projects`가 그것을 스캔한다)
    먼저 쓰면 중간 실패가 **목록에는 보이지만 속이 빈** 프로젝트를 남기고, 그
    상태는 재시작을 넘어 살아남는다.

    **409는 스테이징을 지우지 않는다.** id 충돌은 매니페스트를 읽어야 알 수 있고
    매니페스트는 업로드가 끝난 뒤에나 읽힌다 — 여기서 지우면 사용자는 id 하나
    바꾸려고 번들 전체를 다시 올려야 한다.
    """
    import aipds.app as app_module

    if not _UPLOAD_ID_RE.match(body.upload_id):
        raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)
    staging = _staging()
    upload_id = body.upload_id

    size = await staging.size(upload_id)
    if size is None:
        raise HTTPException(status_code=400, detail=ec.UPLOAD_MISSING)
    if size > MAX_IMPORT_BYTES:
        # 내려받기 **전에** 거절한다 — 상한을 넘는 객체를 디스크로 끌어온 다음에
        # 거절하는 것은 방어가 아니다.
        await staging.delete(upload_id)
        raise HTTPException(status_code=413, detail=ec.EXPORT_TOO_LARGE)

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    bundle = Path(tmp.name)
    try:
        await staging.download_to(upload_id, bundle)
        try:
            manifest = await asyncio.to_thread(_read_manifest, bundle)
        except BundleError as e:
            await staging.delete(upload_id)
            _log.warning("rejected bundle %s: %s", upload_id, e)
            raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)

        pid = body.project_id or manifest["source_project_id"]
        try:
            reject_unsafe_segment(pid)
        except ValueError:
            raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)
        if app_module.registry.is_registered(pid):
            raise HTTPException(status_code=409, detail=ec.PROJECT_EXISTS)

        project = manifest["project"]
        model_id, warnings = await _resolve_model(project.get("model_id"))
        language = project.get("language")
        if language not in ("ko", "en"):
            language = None  # 레지스트리가 "ko"로 확정한다

        try:
            data = await apply_bundle(
                bundle, project_id=pid,
                s3=app_module.s3_store_factory(pid),
                surveys_root=app_module.surveys_root_s3_factory(),
                proto_root=app_module._proto_root())
        except BundleError as e:
            # 번들이 우리가 아는 모양이 아니라는 사실은 `export.json`을 읽을 때만
            # 드러나지 않는다 — 탈출을 시도하는 엔트리 이름, 압축 폭탄, 읽을 수
            # 없는 설문 정의가 모두 여기서 나온다. 그 전부가 같은 400이다:
            # 사용자가 할 일은 다른 파일을 고르는 것이고, 재업로드는 도움이 되지
            # 않으므로 스테이징도 지운다. apply_bundle이 자기가 쓴 것을 이미
            # 되돌렸다.
            await staging.delete(upload_id)
            _log.warning("rejected bundle %s during import: %s", upload_id, e)
            raise HTTPException(status_code=400, detail=ec.EXPORT_INVALID)
        try:
            await write_manifest(app_module.projects_root_s3_factory(), pid,
                                 project.get("name"),
                                 created_at=project.get("created_at"),
                                 model_id=model_id, language=language)
        except Exception:
            # 매니페스트가 없으면 재시작 후 사라지는 프로젝트다. 생성 라우트가
            # 같은 이유로 같은 판단을 한다(routes/projects.py).
            _log.exception("manifest write failed for imported project %s", pid)
            await data.discard()
            raise HTTPException(status_code=500, detail=ec.IMPORT_FAILED)
        app_module.registry.register(pid, project.get("name"),
                                    created_at=project.get("created_at"),
                                    model_id=model_id, language=language)
    finally:
        _cleanup(bundle)

    await staging.delete(upload_id)
    return {"project_id": pid,
            "name": project.get("name"),
            "language": app_module.registry.get_language(pid),
            "model_id": model_id,
            "source_project_id": manifest["source_project_id"],
            "counts": data.counts,
            "warnings": warnings + data.warnings}


def _read_manifest(bundle: Path) -> dict:
    """번들에서 `export.json`만 읽어 검증한다. zip 자체가 열리지 않는 것도
    "우리가 아는 모양이 아니다"이므로 같은 예외로 모은다."""
    try:
        with zipfile.ZipFile(bundle) as zf:
            return parse_manifest(zf.read(MANIFEST_NAME))
    except KeyError as e:
        raise BundleError(f"bundle has no {MANIFEST_NAME}") from e
    except zipfile.BadZipFile as e:
        raise BundleError("upload is not a zip archive") from e


async def _resolve_model(model_id: str | None) -> tuple[str | None, list[dict]]:
    """원본의 모델을 이 인스턴스에서 쓸 수 있는지 본다.

    **조용히 통과시키면 안 된다.** 카탈로그에 없는 모델 id가 매니페스트에 들어가면
    실패는 첫 대화 턴의 `AccessDenied`(IAM 와일드카드 밖)나
    `ValidationException`으로 나타나고, 둘 다 백엔드 로그에만 남는다. env 기본값으로
    강등하면 프로젝트는 돌지만 **고른 적 없는 모델**로 도는 것이므로 그 사실을
    경고로 올린다 — 그것이 이 기능에서 가장 조용한 실패다.
    """
    import aipds.app as app_module

    if model_id is None:
        return None, []
    try:
        allowed = {e.model_id for e in await app_module.model_catalog().displayed()}
    except Exception:
        # 카탈로그를 못 읽는 것으로 임포트를 막지 않는다 — 모델은 폴백이 있고,
        # 여기서 500을 내면 되돌릴 것이 없는 단계에서 실패하는 셈이다.
        _log.warning("model catalog unavailable; importing without a model",
                     exc_info=True)
        return None, [{"code": WARN_MODEL_UNAVAILABLE, "count": 1}]
    if model_id in allowed:
        return model_id, []
    _log.info("imported project's model %r is not selectable here", model_id)
    return None, [{"code": WARN_MODEL_UNAVAILABLE, "count": 1}]
