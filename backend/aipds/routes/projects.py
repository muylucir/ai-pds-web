# backend/pathfinder/routes/projects.py
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from aipds import app as app_module
from aipds import error_codes as ec
from aipds.parsers.state import parse_state_file
from aipds.project_store import write_manifest, delete_project_data
from aipds.proto.cleanup import purge_project_prototypes

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
                            detail=ec.MODEL_NOT_SELECTABLE)


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
                            detail=ec.LANGUAGE_UNSUPPORTED)


@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    # 워크스페이스를 만들기 전에 검증한다 — 거절할 요청 때문에 로컬 디렉토리와
    # 러너를 만들고 되돌리는 것은 낭비다.
    await _validate_model_id(body.model_id)
    _validate_language(body.language)
    # 매니페스트와 레지스트리가 같은 created_at을 갖도록 여기서 확정 —
    # 목록 정렬(생성일 오름차순) 기준이 재시작 전후로 달라지지 않는다.
    created_at = datetime.now(timezone.utc).isoformat()
    # **register가 make_workspace보다 먼저 와야 한다.** 순서가 load-bearing이다.
    #
    # make_workspace는 driver_factory를 부르고, 그 팩토리는 project_language(pid)와
    # project_model(pid)로 **레지스트리를 읽어** 드라이버를 조립한다. 예전에는
    # make_workspace가 먼저였으므로 그 읽기가 아직 등록되지 않은 프로젝트를 보고
    # 폴백값을 집었다 — 언어는 "ko", 모델은 env 기본값. 그리고 그렇게 만들어진
    # 드라이버는 attach된 Workspace가 프로세스 수명 내내 들고 있으므로, 새로 만든
    # 영어 프로젝트의 **모든 턴**이 한국어로 돌았다(2026-08-04 실측).
    #
    # 증상이 헷갈렸던 이유가 여기 있다: 매니페스트·레지스트리·헤더 배지는 모두
    # "en"으로 맞게 들어간다. 어긋나는 것은 드라이버 하나뿐이어서, 화면은 영어인데
    # 대화만 한국어인 상태로 보인다. 모델 쪽은 더 조용하다 — env 폴백이 있어
    # 고른 모델이 아닌 배포 기본 모델로 돌면서 에러도 로그도 남지 않는다.
    #
    # 등록을 앞으로 옮기면 실패 시 되돌릴 것이 하나 늘어난다(아래 except의
    # registry.remove). 그 대가로 "드라이버가 레지스트리를 읽는다"는 사실과
    # 순서가 일치하게 된다 — 팩토리가 값을 인자로 받는 모양으로 바꾸는 대안도
    # 있지만, 그러면 driver_factory·make_workspace·restore 경로가 모두 시그니처를
    # 바꿔야 하고 lazy 초기화 경로(deps.ensure_workspace)는 여전히 레지스트리를
    # 읽는다. 읽는 쪽을 그대로 두고 순서를 맞추는 편이 좁은 수정이다.
    app_module.registry.register(body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id,
                                 language=body.language)
    try:
        workspace = await app_module.make_workspace(body.project_id)
    except Exception:
        # 등록만 남고 워크스페이스가 없는 상태를 남기지 않는다 — 그 상태는 목록에
        # 보이지만 열 수 없는 프로젝트가 된다.
        app_module.registry.remove(body.project_id)
        raise
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
            # 등록도 되돌린다(이제 register가 앞에 있으므로 이것이 필요하다).
            app_module.registry.remove(body.project_id)
            raise HTTPException(status_code=500, detail="project persistence failed")
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
    """전부 삭제(스펙 결정): 러너 stop(베스트에포트) → 프로토타입 실체 정리
    (실패 시 500) → S3 세션+산출물 삭제(실패 시 500, 멱등 재시도) →
    레지스트리 제거."""
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    already_stopped = None
    if app_module.registry.has_workspace(pid):
        already_stopped = app_module.registry.get(pid)
        try:
            await already_stopped.runner.stop()
        except Exception:
            _log.exception("runner stop failed for %s during delete (continuing)", pid)
    # 프로토타입의 **실체**는 S3 프리픽스 밖에 있다: 로컬 빌드 트리, 도는
    # 프리뷰 프로세스와 그 포트, 접근 토큰(파일 + 인메모리 캐시), 빌드 세션,
    # 그리고 버킷 **루트**의 설문 토큰 인덱스(surveys/by-token/). 아래
    # delete_project_data는 projects/{pid}/ 와 sessions/session_{pid}/ 만
    # 지우므로 이것들이 전부 남았다 — 특히 토큰이 살아 있으면 이미 공유한
    # 프리뷰 링크가 삭제된 프로젝트에서도 계속 열린다(프록시는 프로젝트 등록
    # 여부를 보지 않고 proto_host의 토큰·상태만 본다).
    #
    # **S3 삭제보다 먼저** 돌아야 한다. 설문 토큰 인덱스는 역방향 조회가 없어서
    # 문항 파일을 읽어야 회수할 수 있고, 프리픽스를 먼저 지우면 그 인덱스는
    # 어떤 코드로도 도달할 수 없는 채로 영구히 남는다(SurveyStore.purge 참고).
    # 그래서 실패는 여기서 500으로 끊는다: 레지스트리와 S3를 그대로 두어
    # 재시도가 의미를 갖게 한다(모든 단계가 멱등이다).
    durable = app_module.durable_projects_enabled()
    failures = await purge_project_prototypes(
        pid,
        host=app_module.proto_host(),
        sessions=app_module.proto_sessions,
        s3=app_module.s3_store_factory(pid) if durable else None,
        survey_store_factory=app_module.survey_store_factory if durable else None,
    )
    if failures:
        _log.error("prototype cleanup failed for %s: %s", pid, ",".join(failures))
        raise HTTPException(status_code=500,
                            detail=f"prototype cleanup failed: {','.join(failures)}")
    # 워크스페이스 디렉터리는 **경로로** 지운다 — 위 `runner.stop()`에도 rmtree가
    # 있지만 `has_workspace(pid)` 게이트 안에 있고, 그 플래그는 기동 시 복원이
    # `register()`만 하므로 **재시작 뒤 한 번도 열지 않은 프로젝트는 전부
    # False**다(app.purge_local_workspace의 docstring에 실측이 있다). 그리고
    # stop의 실패는 삼켜지므로 드라이버 종료가 실패하면 rmtree도 함께 건너뛴다.
    #
    # S3 삭제보다 먼저 두어 실패가 500으로 끊기게 한다: 문서가 로컬에 남은 채
    # "삭제됐다"고 응답하면 사용자에게 한 약속과 어긋난다.
    try:
        await app_module.purge_local_workspace(pid)
    except Exception:
        _log.exception("workspace purge failed for %s", pid)
        raise HTTPException(status_code=500, detail="workspace purge failed")
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
