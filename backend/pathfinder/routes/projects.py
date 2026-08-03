# backend/pathfinder/routes/projects.py
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from pathfinder import app as app_module
from pathfinder.parsers.state import parse_state_file
from pathfinder.project_store import write_manifest, delete_project_data

_log = logging.getLogger(__name__)

router = APIRouter()

_STATE_PATH = "aiplc-docs/aiplc-state.md"


async def _progress(pid: str) -> dict | None:
    """페이지 내 프로젝트의 진행상황. S3 직접 읽기 — ensure_workspace를 타지
    않는다(목록 조회가 N개 워크스페이스 lazy 초기화를 유발하면 안 됨).
    fail-soft: 어떤 실패도 None으로 강등, 목록 응답은 막지 않는다."""
    if not app_module.durable_projects_enabled():
        return None
    try:
        md = await app_module.s3_store_factory(pid).get(_STATE_PATH)
        state = parse_state_file(md)
    except Exception:
        return None
    if not state.stages:          # 파일은 있지만 스테이지 파싱 결과가 비면 표시할 게 없다
        return None
    return {
        "current_stage": state.current_stage,
        "completed": sum(1 for s in state.stages if s.status == "completed"),
        "total": len(state.stages),
    }

class CreateProject(BaseModel):
    project_id: str
    name: str | None = None
    # 이 프로젝트가 쓸 Bedrock 모델 id. 미지정이면 env 기본값으로 돈다
    # (app.project_model의 폴백 체인).
    model_id: str | None = None
    # 이 프로젝트의 생성물 언어("ko"|"en"). 미지정이면 "ko"로 돈다.
    # UI 언어(pf_lang 쿠키)와 별개다 — 이쪽은 문서·프로토타입·채팅의 언어이고
    # 생성 시점 1회 결정이다.
    language: str | None = None


async def _validate_model_id(model_id: str | None) -> None:
    """카탈로그의 **표시 목록**에 있는지 확인한다.

    등록 목록이 아니라 표시 목록인 이유: display가 꺼진 모델은 관리자가
    의도적으로 내린 것이므로 새 프로젝트가 그것을 고르면 안 된다.

    이 검증이 없으면 임의 문자열이 매니페스트에 들어가고, 실패는 첫 대화
    턴의 AccessDenied(IAM 와일드카드 밖) 또는 ValidationException(존재하지
    않는 프로파일)으로 나타난다 — 둘 다 백엔드 로그에만 남는다.
    """
    if model_id is None:
        return
    allowed = {e.model_id for e in await app_module.model_catalog().displayed()}
    if model_id not in allowed:
        raise HTTPException(status_code=400,
                            detail="선택할 수 없는 모델입니다.")


#: 허용되는 생성물 언어. ProjectRegistry._LANGUAGES와 같은 집합이어야 한다 —
#: 여기서 통과시킨 값이 그쪽 폴백에 걸리면 사용자가 고른 언어가 조용히
#: 무시된다.
_LANGUAGES = ("ko", "en")


def _validate_language(language: str | None) -> None:
    """두 값만 허용한다.

    임의 문자열이 매니페스트에 들어가면 place_rules가 어느 지시 블록을 붙일지
    결정할 수 없고, ProjectRegistry.get_language가 "ko"로 떨어뜨린다 — 즉
    사용자가 고른 언어가 조용히 무시된다. 생성 시점에 막는 것이 그 침묵을
    없애는 유일한 자리다.
    """
    if language is None:
        return
    if language not in _LANGUAGES:
        raise HTTPException(status_code=400,
                            detail="지원하지 않는 언어입니다.")


@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    # 워크스페이스를 만들기 전에 검증한다 — 거절할 요청 때문에 로컬 디렉토리와
    # 러너를 만들고 되돌리는 것은 낭비다.
    await _validate_model_id(body.model_id)
    _validate_language(body.language)
    workspace = await app_module.make_workspace(body.project_id)
    # 매니페스트와 레지스트리가 같은 created_at을 갖도록 여기서 확정 —
    # 목록 정렬(생성일 오름차순) 기준이 재시작 전후로 달라지지 않는다.
    created_at = datetime.now(timezone.utc).isoformat()
    if app_module.durable_projects_enabled():
        try:
            await write_manifest(app_module.projects_root_s3_factory(),
                                 body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id,
                                 language=body.language)
        except Exception:
            # 스펙 결정: 재시작하면 사라질 프로젝트를 조용히 만들지 않는다.
            _log.exception("manifest write failed for %s", body.project_id)
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("workspace cleanup after manifest failure failed")
            raise HTTPException(status_code=500, detail="project persistence failed")
    app_module.registry.register(body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id,
                                 language=body.language)
    app_module.registry.attach(body.project_id, workspace)
    return {"project_id": body.project_id, "name": body.name,
            "model_id": body.model_id,
            # 실제로 돌게 될 언어를 돌려준다(미지정 → "ko"). null을 돌려주면
            # 프론트가 폴백 규칙을 또 알아야 한다.
            "language": app_module.registry.get_language(body.project_id)}

@router.get("/projects")
async def list_projects(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=50)):
    # 페이지 단위 목록 + S3 진행상황(fail-soft). 워크스페이스 lazy 초기화는
    # 유발하지 않는다 — durable 프로젝트 메타데이터(DynamoDB)는 이후 prod 관심사.
    ids = app_module.registry.list_ids()
    total = len(ids)
    page_ids = ids[(page - 1) * size : page * size]
    progresses = await asyncio.gather(*(_progress(pid) for pid in page_ids))
    return {
        "projects": [
            {"project_id": pid, "name": app_module.registry.get_name(pid),
             "created_at": app_module.registry.get_created_at(pid),
             "model_id": app_module.registry.get_model_id(pid),
             "language": app_module.registry.get_language(pid),
             "progress": prog}
            for pid, prog in zip(page_ids, progresses)
        ],
        "total": total,
        "page": page,
        "size": size,
    }

@router.get("/projects/{pid}")
async def get_project(pid: str):
    """프로젝트 하나의 메타데이터. 헤더의 모델 배지가 부르는 곳이다.

    ensure_workspace를 타지 않고 레지스트리만 읽는다 — 배지 하나가 워크스페이스
    lazy 초기화(러너 부팅)를 유발하면 안 된다. list_projects의 _progress가
    같은 이유로 S3를 직접 읽는다.
    """
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    return {"project_id": pid,
            "name": app_module.registry.get_name(pid),
            "created_at": app_module.registry.get_created_at(pid),
            "model_id": app_module.registry.get_model_id(pid),
            "language": app_module.registry.get_language(pid)}

@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    """전부 삭제(스펙 결정): VM stop(베스트에포트) → S3 세션+산출물 삭제
    (실패 시 500, 멱등 재시도) → 레지스트리 제거."""
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    already_stopped = None
    if app_module.registry.has_workspace(pid):
        already_stopped = app_module.registry.get(pid)
        try:
            await already_stopped.runner.stop()
        except Exception:
            _log.exception("runner stop failed for %s during delete (continuing)", pid)
    if app_module.durable_projects_enabled():
        try:
            await delete_project_data(app_module.session_s3_factory(),
                                      app_module.projects_root_s3_factory(), pid)
        except Exception:
            _log.exception("S3 delete failed for %s", pid)
            raise HTTPException(status_code=500, detail="project delete failed")
    removed = app_module.registry.remove(pid)
    # 역방향 레이스 대비: has_workspace가 False라 위 stop 블록을 건너뛰었더라도,
    # S3 삭제 await 도중 동시 ensure_workspace가 부팅을 마치고 attach했을 수
    # 있다. 마지막 remove가 반환한 워크스페이스가 그 정리 지점이 된다 — 이미
    # 위에서 stop한 것과 동일 객체면(레이스가 없었던 정상 경로) 중복 stop을
    # 피하고, 다른 객체면(늦게 attach된 워크스페이스) stop해 VM이 새지 않게 한다.
    if removed is not None and removed is not already_stopped:
        try:
            await removed.runner.stop()
        except Exception:
            _log.exception("runner stop failed for %s during final registry removal (continuing)", pid)
    return {"deleted": True}
