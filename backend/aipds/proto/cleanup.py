# backend/aipds/proto/cleanup.py — 프로젝트 단위 프로토타입 정리.
#
# 프로토타입은 **기록과 실체가 다른 곳에 산다.** S3의 projects/{pid}/prototypes/**
# 는 기록(스펙·트랜스크립트·handoff·설문)이고, 실체는 EC2 로컬 디스크의
# {proto_root}/{pid}/{slug}/(빌드 트리 + 호스팅 프로세스 + 접근 토큰)와 백엔드
# 인메모리(빌드 세션·토큰 캐시)에 있다. 그래서 프로젝트 삭제가
# projects/{pid}/ 프리픽스만 지우면 실체가 전부 남는다 — 수백MB~GB짜리 빌드
# 트리, 계속 도는 프리뷰 프로세스와 그것이 쥔 포트, 그리고 **이미 공유된
# 프리뷰 링크가 여전히 열리는 상태**(프록시는 프로젝트 등록 여부를 보지 않고
# proto_host의 토큰·상태만 본다).
#
# 여기서 도는 순서는 프로토타입 개별 리셋(routes/prototypes.py의
# reset_prototype)과 같다. 그쪽 순서가 load-bearing인 이유가 그대로 적용된다:
# 설문 토큰 인덱스(surveys/by-token/, 버킷 루트)는 역방향 조회가 없어서
# **문항 파일을 읽어야** 어떤 토큰이 이 슬러그를 가리키는지 알 수 있다. 그래서
# 설문 purge가 S3 프로젝트 프리픽스 삭제보다 반드시 먼저 와야 한다 — 순서가
# 뒤집히면 그 인덱스는 어떤 코드로도 도달할 수 없는 채로 영구히 남는다.
from __future__ import annotations

import logging
from typing import Any, Callable

_log = logging.getLogger(__name__)

#: 프로젝트 스코프 스토어(projects/{pid}/) 기준 프로토타입 기록 프리픽스.
_PROTO_PREFIX = "prototypes/"


async def _slugs_from_s3(s3: Any) -> set[str]:
    """S3 기록에서 슬러그를 뽑는다. 설문 트리가 이 아래 있으므로
    (survey.store.survey_prefix == "prototypes/{slug}/survey/") 토큰 회수
    대상은 이 열거로 전부 덮인다."""
    out: set[str] = set()
    for key in await s3.list(_PROTO_PREFIX):
        slug, sep, _ = key[len(_PROTO_PREFIX):].partition("/")
        if sep and slug:
            out.add(slug)
    return out


async def purge_project_prototypes(
    project_id: str,
    *,
    host: Any,
    sessions: dict,
    s3: Any | None = None,
    survey_store_factory: Callable[[str, str], Any] | None = None,
) -> list[str]:
    """이 프로젝트의 **모든** 슬러그에 리셋 경로를 돌린다.

    슬러그당 순서: 빌드 세션 종료 → 설문 purge(토큰 인덱스 회수 포함) → 빌드
    트리/접근 토큰 purge.

    반환값은 실패 라벨 목록이고 빈 목록이 전부 성공이다. 호출부(프로젝트 삭제
    라우트)는 실패가 하나라도 있으면 **S3 프로젝트 프리픽스를 지우지 않고**
    500을 내야 한다: 설문 purge가 실패한 상태에서 프리픽스를 지우면 문항 파일이
    사라져 토큰 인덱스를 회수할 방법이 영구히 없어진다(SurveyStore.purge의
    docstring이 같은 이유로 그쪽 경로에도 게이트를 둔다).

    모든 단계가 멱등이라 재시도가 수렴한다. 그래도 풀리지 않는 잔여물(예:
    node_modules 깊은 곳의 권한 오류로 rmtree가 남기는 트리)은 운영자가
    `{proto_root}/{pid}` 를 직접 지우고 다시 삭제하면 된다 — 조용히 남기는
    대신 실패를 보이게 하는 쪽을 고른다.

    `s3`/`survey_store_factory`가 None이면 설문 단계를 건너뛴다(버킷 미설정
    로컬·테스트: durable_projects_enabled()가 False인 경우). 그때도 로컬 실체
    정리는 그대로 돈다 — 빌드 트리와 세션은 S3와 무관하게 존재한다.
    """
    slugs: set[str] = set()
    try:
        slugs |= set(host.slugs(project_id))
    except Exception:
        _log.exception("local prototype listing failed for %s", project_id)
        return ["slug-list"]
    slugs |= {slug for (pid, slug) in list(sessions) if pid == project_id}
    if s3 is not None:
        try:
            slugs |= await _slugs_from_s3(s3)
        except Exception:
            # 목록을 못 읽으면 무엇을 남기는지 알 수 없다. 부분 정리 후 S3를
            # 지우면 안 읽힌 슬러그의 토큰 인덱스가 고아가 되므로 여기서 멈춘다.
            _log.exception("prototype record listing failed for %s", project_id)
            return ["slug-list"]

    failures: list[str] = []
    for slug in sorted(slugs):
        session = sessions.get((project_id, slug))
        if session is not None:
            try:
                await session.close()
            except Exception:
                # 세션이 안 닫히면 claude 서브프로세스와 동시 빌드 슬롯이 샌다.
                _log.exception("delete: session close failed: %s/%s", project_id, slug)
                failures.append(f"session:{slug}")
            else:
                sessions.pop((project_id, slug), None)

        if survey_store_factory is not None:
            try:
                await survey_store_factory(project_id, slug).purge()
            except Exception:
                _log.exception("delete: survey purge failed: %s/%s", project_id, slug)
                failures.append(f"survey:{slug}")
                # 이 슬러그는 더 진행하지 않는다 — 리셋 경로의 게이트와 같은
                # 판단이다. 설문이 남은 채 빌드 트리를 지우면 재시도가 회수할
                # 문항은 그대로인데 호스팅할 실체가 없는 상태가 된다.
                continue

        try:
            await host.purge(project_id, slug)
        except Exception:
            _log.exception("delete: build-tree purge failed: %s/%s", project_id, slug)
            failures.append(f"build-tree:{slug}")

    # 부모 디렉터리(`{proto_root}/{pid}`)는 슬러그 루프 **뒤에** 지운다.
    #
    # 순서가 load-bearing이다: 슬러그별 `purge`는 각각 `stop()`을 먼저 부르는데,
    # 부모를 먼저 지우면 도는 `npm start` 밑에서 트리가 사라져 프로세스가 고아가
    # 되고 포트를 계속 물고 있다(`ProtoHost.purge`의 docstring이 경고하는 그
    # 실패). `purge_project`는 아무것도 멈추지 않으므로 이 위치가 유일하게 안전한
    # 자리다.
    #
    # 실패가 있으면 건너뛴다: 남은 슬러그의 트리를 부모째로 지우면 재시도가
    # 회수해야 할 실체가 사라진다 — 슬러그별 게이트와 같은 판단이다.
    if not failures:
        try:
            await host.purge_project(project_id)
        except Exception:
            _log.exception("delete: prototype root purge failed: %s", project_id)
            failures.append("proto-root")
    return failures
