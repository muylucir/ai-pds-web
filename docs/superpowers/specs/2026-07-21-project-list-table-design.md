# 프로젝트 목록 테이블 + 페이지네이션 설계

날짜: 2026-07-21
상태: 승인됨 (구현 대기)

## 배경과 목표

프로젝트 목록이 현재 카드 그리드(`ProjectList.tsx`)로, 이름·ID만 보인다. 이를
**테이블(프로젝트 ID · 프로젝트명 · 진행상황)**로 바꾸고 **페이지네이션**을 적용한다.

사용자 결정:
- 진행상황 데이터는 **백엔드 목록 API 확장**으로 (프론트 N+1 호출 아님)
- **백엔드 페이지네이션** (page/size 쿼리, 해당 페이지만 계산)
- 진행상황 표기는 **현재 스테이지명 + 단계 카운트** — 예: `Envision (2/8)`

## §1 백엔드 — GET /projects 확장

**쿼리 파라미터**: `page`(1-base, 기본 1, 최소 1), `size`(기본 10, 최소 1, 최대 50).
범위 밖 값은 422가 아니라 경계로 클램프하지 않고 FastAPI Query 검증(ge/le)으로 422.

**응답 형태**:
```json
{
  "projects": [
    {
      "project_id": "p-1",
      "name": "면세 기획전",
      "progress": {"current_stage": "Envision", "completed": 2, "total": 8}
    },
    {"project_id": "p-2", "name": null, "progress": null}
  ],
  "total": 23,
  "page": 1,
  "size": 10
}
```

**페이지 슬라이스**: `registry.list_ids()`(등록 순서 보존)를 `[(page-1)*size : page*size]`로
자른다. `total`은 전체 등록 수. 빈 페이지(범위 초과)는 `projects: []`로 정상 응답.

**진행상황 계산** (페이지 내 프로젝트만):
- `s3_store_factory(pid).get("aiplc-docs/aiplc-state.md")`로 S3 **직접 읽기** —
  `ensure_workspace`를 타지 않는다(목록 조회가 N개 워크스페이스 lazy 초기화를
  유발하면 안 됨). 기존 `parse_state_file`(parsers/state.py) 재사용.
- `progress` = `{current_stage: state.current_stage, completed: status=="completed"인
  스테이지 수, total: 전체 스테이지 수}`.
- fail-soft: 파일 없음(FileNotFoundError)/파싱 실패/S3 예외/`PATHFINDER_S3_BUCKET`
  미설정 → 해당 프로젝트 `progress: null`. 목록 응답은 절대 실패하지 않는다.
- 페이지 내 N건은 `asyncio.gather`로 병렬 읽기.
- `current_stage`가 None이고 스테이지가 있으면 `current_stage: null` 그대로 반환
  (프론트가 카운트만 표시).

**하위 호환**: 파라미터 없는 `GET /projects`는 page=1/size=10으로 동작. 기존
프론트 외 소비자(e2e 등)는 `projects` 배열 키가 유지되므로 깨지지 않는다.

## §2 프론트엔드 — 테이블 + 페이지네이션

**API 클라이언트** (`lib/api/client.ts`, `lib/api/types.ts`):
```ts
export interface ProjectProgress { current_stage: string | null; completed: number; total: number; }
export interface ProjectSummary { project_id: string; name: string | null; progress?: ProjectProgress | null; }
export interface ProjectPage { projects: ProjectSummary[]; total: number; page: number; size: number; }
listProjects(page = 1, size = 10): Promise<ProjectPage>
```
(`createProject` 반환 타입 `ProjectSummary`는 `progress` optional이라 무변경.)

**ProjectList.tsx 재작성** — 카드 그리드 → 테이블:
- 컬럼: 프로젝트 ID · 프로젝트명 · 진행상황 · 삭제(아이콘 버튼).
- 행 전체가 대시보드 링크(`/projects/{pid}/dashboard`) — 삭제 버튼은 클릭 전파 차단.
- 진행상황 셀: `progress` 있으면 `{current_stage} ({completed}/{total})`,
  `current_stage`가 null이면 `({completed}/{total})`만, `progress`가 null이면 `—`.
- 삭제 확인 다이얼로그(Escape 닫기 포함)는 기존 로직 그대로 이식.
- 빈 목록 문구("아직 생성된 프로젝트가 없습니다…") 유지 — 단 total==0일 때만.

**페이지네이션 컨트롤** (테이블 하단):
- `‹ 이전 · {page} / {totalPages} · 다음 ›` + `총 {total}건`. totalPages =
  `max(1, ceil(total/size))`. 첫/마지막 페이지에서 해당 버튼 disabled.
- 페이지 상태는 `app/page.tsx`가 소유. 페이지 변경 시 `listProjects(page)` 재호출.
- 생성 시: 현재 페이지 리로드. 삭제 시: 현재 페이지 리로드하되, 삭제로 현재
  페이지가 비고 page>1이면 page-1로 이동 후 리로드.

## §3 테스트

**백엔드** (`tests/test_routes_projects_list.py` 확장):
- 페이지 슬라이스: 3건 등록 + size=2 → page1: 2건/page2: 1건/page3: 빈 배열, total=3.
- size 상한: size=51 → 422. page=0 → 422.
- progress: state 파일 있는 프로젝트 → current_stage/completed/total 정확;
  파일 없음 → null; 파싱 실패(깨진 마크다운) → null; 버킷 미설정 → null.
- ensure_workspace 미호출: 목록 조회 후 `registry.has_workspace(pid) == False` 유지.
- 하위 호환: 파라미터 없는 호출이 page=1/size=10으로 동작.

**프론트** (`ProjectList.test.tsx` 재작성 + `page.tsx` 관련 MSW 갱신):
- 테이블 렌더: ID/이름/진행 셀 표시, progress null → `—`.
- 행 링크 href, 삭제 버튼 → 다이얼로그 → confirm 흐름(기존 테스트 이식).
- 페이지네이션: 다음/이전 클릭 → onPageChange 호출; 경계에서 disabled.
- 빈 목록 문구 (total==0).
- MSW 핸들러를 새 응답 형태(`{projects, total, page, size}`)로 갱신 — 영향받는
  기존 테스트(있다면) 함께 수정.

## 범위 제외 (YAGNI)

- 정렬/검색/필터 — 요청 없음.
- 생성일·소유자 등 추가 메타데이터 — 백엔드에 데이터 없음(기존 주석: DynamoDB는
  후일 prod 관심사).
- URL 쿼리 동기화(?page=2) — 요청 없음, 컴포넌트 state로 충분.

## Open Questions

없음.
