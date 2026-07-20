# 프로젝트 목록 영속화 + 프로젝트 삭제 — 설계 스펙

날짜: 2026-07-20
상태: 사용자 설계 승인 완료 ("이견없음 진행")

## 배경 / 문제

- 채팅 세션(`sessions/session_<pid>/…`)과 산출물(`projects/<pid>/…`)은 S3에 영속화되어 있지만, **프로젝트 목록·이름은 `ProjectRegistry`의 인메모리 dict 두 개**(`_projects`, `_names`)에만 있다 (`backend/pathfinder/workspace.py:59-62`).
- 백엔드 재시작 시 레지스트리가 비어 모든 라우트가 `deps.py`의 404 게이트("unknown project")에 막힌다. S3 데이터는 멀쩡한데 **입구만 휘발**하는 상태 (실사례: `drill-st1`).
- 프로젝트 삭제 기능은 백엔드·프론트 어디에도 없다.

## 승인된 요구사항 (사용자 결정)

1. **삭제 범위 = 전부 삭제**: 레지스트리 해제 + VM 정지 + S3의 `sessions/session_<pid>/*`·`projects/<pid>/*` 오브젝트 전부 삭제. 되돌릴 수 없음.
2. **sandbox 복원 시점 = lazy**: 기동 시에는 목록만 복원, sandbox(MicroVM)는 그 프로젝트에 첫 요청이 올 때 생성.
3. **삭제 UI = 카드 버튼 + 확인 다이얼로그**: ID 재입력 없이 경고 문구 + 확인/취소.
4. 매니페스트 쓰기 실패 시 `POST /projects`는 500으로 실패 (조용한 휘발 프로젝트 생성 금지).
5. 삭제 시 VM stop 실패는 무시하고 진행 — S3 삭제가 본질. S3 삭제 실패는 500 (멱등이라 재시도 안전).

## 아키텍처 (승인안 A: 프로젝트별 매니페스트 + prefix 스캔)

### 1. 데이터 모델

- **매니페스트 키**: `projects/<pid>/project.json` — 프로젝트 데이터와 같은 prefix에 두어 **삭제가 원자적**(prefix 전체 삭제로 끝).
- **내용**: `{"project_id": str, "name": str | null, "created_at": "<ISO8601 UTC>"}`
- **기동 복원**: FastAPI **lifespan** (현재 앱에는 lifespan/startup 훅이 없음 — 신설)에서 `projects/` prefix LIST 1회 → `^<pid>/project\.json$` 패턴 키만 골라 **병렬 GET**(`asyncio.gather`, `list_history`와 동일 패턴) → `registry.register(pid, name)`.
- **버킷 미설정 시**(`PATHFINDER_S3_BUCKET` 빈 값 — 로컬/테스트): 매니페스트 쓰기·복원 모두 생략. 기존 인메모리 동작 그대로.
- 복원 실패(S3 장애 등)는 로그 + 빈 목록으로 기동 (부팅을 막지 않음).

### 2. ProjectRegistry 재구성 (`workspace.py`)

"아는 프로젝트"와 "살아있는 워크스페이스"를 분리:

```python
class ProjectRegistry:
    def __init__(self):
        self._names: dict[str, str | None] = {}        # 등록된 전체 (복원 포함)
        self._workspaces: dict[str, Workspace] = {}    # sandbox가 붙은 것만

    def register(self, project_id, name=None) -> None      # sandbox 없이 등록
    def attach(self, project_id, sandbox) -> Workspace     # Workspace 생성·연결 (등록 선행 필수)
    def get(self, project_id) -> Workspace                 # 살아있는 워크스페이스만; KeyError
    def is_registered(self, project_id) -> bool
    def has_workspace(self, project_id) -> bool
    def remove(self, project_id) -> Workspace | None       # 등록·워크스페이스 모두 제거, 있던 Workspace 반환
    def list_ids(self) -> list[str]                        # _names 기준 (등록 순서)
    def get_name(self, project_id) -> str | None           # 미등록이면 KeyError
```

기존 `create(pid, sandbox, name)`은 **제거**하고 호출부(`projects.py` 한 곳)를 `register + attach`로 바꾼다 — 합성 메서드를 남기면 "등록만" 경로와 "등록+연결" 경로가 이중화되어 혼동을 만든다.

### 3. lazy sandbox: `deps.get_workspace` → async `ensure_workspace`

```python
_locks: dict[str, asyncio.Lock]   # pid별 이중 부팅 방지

async def ensure_workspace(pid: str) -> Workspace:
    1. registry.get(pid) 성공 → 반환 (fast path)
    2. is_registered(pid) 거짓 → HTTPException 404 "unknown project"
    3. 등록만 된 경우: pid별 Lock 획득 후 재확인(double-check) →
       app_module.make_sandbox(pid) → registry.attach(pid, sandbox) → 반환
    4. 부팅 실패: 로그 + HTTPException 503 "project workspace unavailable";
       레지스트리 등록은 유지 (다음 요청이 재시도)
```

**호출부 변경**: `artifacts.py`(5곳), `history.py`(1), `answers.py`(1), `uploads.py`(1), `discovery.py`(2), `turns.py`(4) — 전부 async 라우트이므로 `get_workspace(pid)` → `await ensure_workspace(pid)` 기계적 치환. `deps.get_workspace`(sync)는 제거.

### 4. `POST /projects` 변경 (`projects.py`)

1. 중복 검사: `registry.is_registered(pid)` → 있으면 409 (기존과 동일 의미)
2. `make_sandbox(pid)` (생성 시에는 기존처럼 즉시 부팅 — lazy는 "복원된" 프로젝트에만 해당)
3. **매니페스트 쓰기**: 버킷 설정 시 `s3_store_factory(pid).put("project.json", …)` — prefix가 `projects/<pid>/`이므로 키는 상대경로 `project.json`. **실패 시 sandbox.stop() 후 500** (베스트에포트 정리; 프로젝트는 등록하지 않음)
4. `registry.register + attach` → 200 `{project_id, name}`

### 5. `DELETE /projects/{pid}` 신설 (`projects.py`)

```
1. is_registered(pid) 거짓 → 404
2. has_workspace(pid) 참이면 sandbox.stop() — 예외는 로그만 하고 계속 (VM 정지 실패가 데이터 삭제를 막지 않음)
3. S3 삭제 (버킷 설정 시):
   - sessions store에서 delete_prefix("session_<pid>/")
   - projects store(prefix projects/<pid>/)에서 delete_prefix("")
   실패 → 500 반환, 레지스트리 유지 (멱등 재시도 가능)
4. registry.remove(pid) → {"deleted": true}
```

### 6. `S3Store.delete_prefix` 신설 (`s3store.py`)

```python
async def delete_prefix(self, prefix: str) -> int:
    # list_objects_v2 페이지네이션 → delete_objects 배치(1000개/호출)
    # 반환: 삭제한 오브젝트 수. Protocol(S3StoreLike)에도 추가,
    # FakeS3Store(tests/fakes)에도 동일 시그니처 구현.
```

기존 `S3Store.list`는 그대로 활용(네임스페이스 내부 상대 키). `delete_prefix`도 스토어의 `self._prefix` 네임스페이스 안에서 동작.

### 7. IAM 참고 (스코프 밖, 배포 시 확인)

백엔드 호스트 롤(vscode-role)이 버킷의 `s3:DeleteObject`(`sessions/*`·`projects/*`)를 가져야 한다. MicroVM 실행 롤은 변경 없음(이미 `sessions/*` DeleteObject 보유 — 백엔드와는 별개 주체). CDK 스택은 백엔드 롤을 관리하지 않으므로 코드 변경은 없고, 배포 환경에서 권한만 확인.

### 8. 프론트 (`ProjectList.tsx`, `client.ts`, `types.ts` 불변)

- `client.ts`: `deleteProject(pid): Promise<void>` — `DELETE /projects/<pid>` (request 헬퍼 재사용)
- `ProjectList`에 삭제 흐름 추가:
  - 카드(li) 우상단 휴지통 버튼 — 카드 전체가 `Link`이므로 버튼은 Link **밖**, li 안 absolute 배치 (클릭 전파 차단)
  - 클릭 → 확인 다이얼로그(모달): 제목 "'{name ?? pid}' 프로젝트 삭제", 본문 "채팅 기록과 모든 문서가 영구 삭제되며 되돌릴 수 없습니다.", [삭제](빨강)/[취소]
  - 확인 → `deleteProject` 호출, 진행 중 버튼 disabled, 성공 시 부모 `onDeleted`(= 목록 reload) 호출, 실패 시 다이얼로그 안에 에러 문구 유지
  - `ProjectList` props에 `onDeleted: () => void` 추가; `app/page.tsx`에서 `reload` 전달
- 접근성: 다이얼로그 `role="dialog"` `aria-modal` + Escape 닫기 (워크스페이스 bottom-sheet와 동일 패턴)

## 데이터 흐름 요약

```
[기동]   lifespan → S3 projects/ 스캔 → register(pid, name)  (sandbox 없음)
[첫 요청] ensure_workspace → lock → make_sandbox → attach → 처리
[생성]   POST /projects → sandbox 부팅 → 매니페스트 put (실패 시 500) → register+attach
[삭제]   DELETE → stop(베스트에포트) → S3 delete_prefix ×2 (실패 시 500) → remove
```

## 에러 처리 표

| 상황 | 동작 |
|---|---|
| 기동 복원 실패 | 로그 + 빈 목록 기동 |
| 매니페스트 put 실패 | POST 500, sandbox 정리(베스트에포트), 미등록 |
| lazy 부팅 실패 | 해당 요청 503, 등록 유지 → 재시도 가능 |
| 삭제 중 stop 실패 | 로그만, 계속 진행 |
| 삭제 중 S3 실패 | 500, 레지스트리 유지 → 재시도 |
| 미등록 pid DELETE | 404 |

## 테스트 계획

**백엔드 (pytest, FakeS3Store + fake sandbox):**
- Registry: register/attach/get/remove/list_ids/get_name, 등록-없이-attach 금지
- 복원: FakeS3에 매니페스트 2개 → lifespan 복원 → list에 등장, sandbox 없음; 손상 매니페스트는 건너뜀; 버킷 미설정이면 복원 생략
- ensure_workspace: 살아있으면 그대로, 등록만이면 부팅+attach 1회(동시 2요청도 부팅 1회 — lock), 미등록 404, 부팅 실패 503+등록유지
- POST: 매니페스트 키·내용 검증; put 실패 시 500 + stop 호출 + 미등록
- DELETE: FakeS3에서 sessions/·projects/ 키 소멸, stop 호출, 404, S3 실패 시 500+등록유지, 멱등 재시도
- delete_prefix: 페이지네이션 경계(>1000개 배치 분할)

**프론트 (vitest + msw):**
- 카드에 삭제 버튼 렌더, 클릭 시 다이얼로그(문구 포함), 취소 시 닫힘·DELETE 미호출
- 확인 시 DELETE 호출 + onDeleted 호출, 실패 시 에러 문구·다이얼로그 유지
- 삭제 버튼 클릭이 카드 Link 내비게이션을 트리거하지 않음

**스코프 밖:** 프로젝트 이름 변경, 소프트 삭제/복구, 생성시각 UI 노출, DynamoDB.
