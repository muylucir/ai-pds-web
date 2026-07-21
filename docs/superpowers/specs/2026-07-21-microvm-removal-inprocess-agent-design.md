# MicroVM 제거 — Strands 에이전트 백엔드 내장 설계

날짜: 2026-07-21
상태: 승인 대기

## 배경과 목표

현재 실 AI 턴은 도쿄 Lambda MicroVM 안의 하네스 서버(harness/)에서 Strands
에이전트가 실행하고, 백엔드는 VM 부팅/재개/정지(microvm_control*)와 HTTP 중계
(HarnessClient)를 담당한다. 이 구조를 제거하고 **Strands 에이전트를 백엔드
프로세스 안에서 직접 실행**한다.

동기(사용자 확인):
- 운영 복잡도 축소 — CDK 이미지 빌드·토큰 민팅·VM 라이프사이클(suspend/expire) 관리 제거
- 지연시간 개선 — 턴마다 VM 부팅/복원으로 인한 첫 응답 지연 제거
- 격리 불필요 판단 — 에이전트 도구가 워크스페이스 상대 파일 조작뿐이라 VM 격리가 과함

명시적 결정(사용자):
- **S3 유지** — 프로젝트 파일 source of truth, 복원·세션 이어가기·삭제 기반 그대로
- **하네스는 Strands 에이전트 로직만 남기고 이전** — 나머지 전부 삭제
- **Bedrock 인증은 호스트 자격증명** — 백엔드가 도는 EC2/컨테이너의 IAM 롤/프로필 체인
- **워크스페이스는 로컬 디렉토리 + S3 sync** — 기존 VM↔S3 패턴 재사용
- **local 모드(스크립트된 가짜 에이전트) 제거** — 모드 분기 자체를 없앰
- **Sandbox 추상화 해체(접근 B)** — ABC 제거, Workspace가 구성요소 직접 소유
- **e2e는 실 Bedrock** — Playwright e2e는 자격증명 있는 환경에서만
- **CDK 축소 유지** — S3 버킷 + 백엔드 롤만 남긴 최소 스택

## §1 아키텍처

### 이전 (harness/ → backend/pathfinder/agent/)

| 원본 | 이동 후 | 비고 |
|---|---|---|
| `harness/strands_driver.py` | `backend/pathfinder/agent/driver.py` | Strands 루프·세션·interrupt 처리. 핵심 기능 |
| `harness/aiplc_tools.py` | `backend/pathfinder/agent/tools.py` | 5개 도구(ask_questions/report_stage/submit_document/file_*) |
| `harness/events.py` | 삭제 — `pathfinder.models`의 `AgentEvent`로 통합 | 백엔드에 동일 모델 존재(현 base.py). 한 벌만 유지 |

- `AgentEvent`/`TurnResult`는 `pathfinder/sandbox/base.py`에서 `pathfinder/models.py`로 이동
  (Sandbox ABC는 소멸). 이벤트 계약(kind/text/path/payload)은 무변경 — 프론트 무영향.
- 시스템 프롬프트의 룰 소스: 기존에는 VM 이미지에 구워진 `/workspace/aiplc-rules`를
  읽었다. 이제 **`PATHFINDER_RULES_DIR`**(기본값 `<repo>/files/aiplc-rules`)에서 백엔드가
  직접 읽는다. 룰은 프로젝트별 워크스페이스에 복사하지 않고 공용 디렉토리에서 읽기 전용으로
  로드한다(룰은 데이터, 프로젝트 산출물 아님 — S3 sync 대상에서도 제외 유지).
- `file_read` 도구의 룰 상세 온디맨드 로드(`aiplc-rules/...` 상대경로)는 워크스페이스가
  아닌 룰 디렉토리로 위임되도록 tools.py에서 경로 라우팅한다: `aiplc-rules/` 프리픽스는
  RULES_DIR(읽기 전용), 그 외는 프로젝트 워크스페이스.

### 삭제

- `harness/` 디렉토리 전체 (에이전트 3파일 이전 후): app.py, serve.py, hooks.py,
  claude_driver.py, globmatch.py(백엔드에 동일본 존재), Dockerfile, requirements.txt, tests/
- `backend/pathfinder/sandbox/`: base.py(ABC), local.py, microvm.py, microvm_control.py,
  microvm_control_aws.py, harness.py — **s3store.py, pathsafe.py, globmatch.py는 유지**
  (각각 `pathfinder/s3store.py`, `pathfinder/pathsafe.py`, `pathfinder/globmatch.py`로 승격,
  sandbox/ 패키지 소멸)
- `infra/`: MicroVM 이미지·빌드 롤·하네스 asset·로그 그룹·package-harness.sh
- 백엔드 microvm 관련 테스트 및 fakes(harness_app.py 등) — s3store/파서 등 유지되는
  구성요소의 테스트는 존속

### 새 구조

```
backend/pathfinder/
  agent/
    driver.py      # StrandsDriver (구 strands_driver.py, 워크스페이스=로컬 디렉토리)
    tools.py       # build_tools (구 aiplc_tools.py, 룰 디렉토리 라우팅 추가)
  runner.py        # AgentRunner — 구 MicroVMSandbox의 턴 오케스트레이션 승계
  workspace.py     # Workspace가 AgentRunner + S3Store 직접 소유 (Sandbox 소멸)
  s3store.py, pathsafe.py, globmatch.py   # sandbox/에서 승격
  models.py        # AgentEvent/TurnResult 합류
```

### AgentRunner (구 MicroVMSandbox의 승계자)

```python
class AgentRunner:
    def __init__(self, project_id, driver: StrandsDriver, s3: S3StoreLike,
                 local_root: Path, session: dict): ...
    # 파일 계약 ops: S3 직접 (기존과 동일, 부팅 개념 없음)
    async def read_file/write_file/list_files
    # 턴: restore → in-process 실행 → sync
    async def send_message(text) -> AsyncIterator[AgentEvent]
    async def send_answers(answers) -> AsyncIterator[AgentEvent]
    async def pending() -> str | None
    async def stop()   # 로컬 워크스페이스 정리(shutil.rmtree)
```

승계되는 것: `_sync_workspace_to_s3`(audit.md redaction-at-rest 포함),
`_restore_workspace_from_s3`, interrupt id 소유(`_pending_interrupt_id`),
턴 직렬화(`_turn_active` → "turn already in progress"), done/error 전 sync 완료
(fail-closed). 소멸하는 것: boot/resume/status 상태기계, `_boot_lock`,
harness_factory/HarnessClient, 토큰 민팅, on_stop(httpx) 훅.

`input_holder`(advisory 힌트)는 Workspace 필드로 이동한다 — 현재 라우트가 Sandbox에서
읽는 소프트 메타데이터이므로 계약 무변경.

## §2 턴 플로우

```
POST /projects/{pid}/message (또는 SSE /events)
  → ensure_workspace(pid)               # lazy 생성: 로컬 디렉토리 mkdir (VM 부팅 소멸)
  → AgentRunner.send_message(text)
      1. _restore_workspace_from_s3()    # S3 → local_root (S3 unconditionally wins, 멱등)
      2. StrandsDriver.run(text, session) # in-process, 도구는 local_root 파일 I/O
      3. done/error 관측 시 _sync_workspace_to_s3()  # 터미널 이벤트 yield 전 완료
  → 이벤트 스트림 (redaction은 기존 라우트 seam 그대로)
```

- **파일 라우트**(artifacts/answers/uploads/discovery)는 지금과 동일하게 S3 직접
  read/write — 턴 없이 워크스페이스를 만지는 시맨틱 유지. 턴 시작 시 restore가
  S3 변경분을 로컬로 당겨오므로 정합성 모델도 동일.
- **세션 연속성**: `S3SessionManager`(strands SDK)를 백엔드 프로세스에서 직접 사용.
  session 디스크립터(session_id/bucket/region/prefix)는 지금 app.py가 만들던 것과 동일.
  `/history`(session_history.py)와 pending interrupt 복원 로직 무변경.
- **백엔드 재시작 복구**: 로컬 워크스페이스는 휘발로 취급한다. 재시작 후 첫 요청 시
  ensure_workspace가 디렉토리를 재생성하고 턴 시작 시 S3에서 restore — VM expiry 복구와
  동일한 모델이며, 오히려 상태기계가 없어 단순해진다.
- **동시성**: 프로젝트당 턴 직렬화는 기존 `_turn_active` 계약 유지. 프로젝트 간 동시 턴은
  각자 독립 로컬 디렉토리라 간섭 없음. Bedrock 처리량은 호스트 자격증명의 계정 쿼터를 따름
  (워크숍 소수 동시 사용자 모델 — 기존과 동일 가정).

## §3 테스트 전략

- **백엔드 유닛**: StrandsDriver의 기존 `agent_factory` 주입점을 유지 — 테스트는 fake
  agent factory로 AWS 없이 전 계약 검증. harness/tests 중 strands_driver·aiplc_tools
  대상 테스트는 backend/tests로 **이식**(삭제 대상은 app/serve/hooks/claude_driver
  테스트만). MicroVMSandbox 테스트 중 sync/restore·interrupt 소유·직렬화·redaction
  시나리오는 AgentRunner 테스트로 이식, boot/resume/expire 시나리오는 폐기. 존속 대상
  (s3store, 파서, 라우트, project_store) 테스트는 import 경로만 갱신.
- **프론트 유닛(Vitest+MSW)**: API 계약 무변경이므로 영향 없음.
- **e2e(Playwright)**: 실 Bedrock으로 실행(자격증명 필요, INTEGRATION 표기 유지).
  AI 응답 텍스트 내용 단언은 제거하고 구조 단언(SSE 스트림 완료, 카드 렌더, 파일 생성)
  위주로 조정.

## §4 인프라·설정

- **CDK 축소**: 스택에 남는 것 = S3 버킷(그대로) + 백엔드 실행 롤. 백엔드 롤 권한:
  - `bedrock:InvokeModel(WithResponseStream)` — 기존 인퍼런스 프로파일 + 파운데이션 모델 ARN 셰이프
  - S3 — `projects/*` **및** `sessions/*` 프리픽스 (기존 VM 롤은 sessions/*만이었으나,
    이제 한 프로세스가 둘 다 접근: 백엔드는 원래 projects/*를 만졌고 세션도 직접 쓰게 됨)
  - 제거: MicroVM 이미지, 빌드 롤, 하네스 asset, CloudWatch 로그 그룹, package-harness.sh
- **env 정리**:
  - 삭제: `PATHFINDER_SANDBOX`, `PATHFINDER_VM_REGION`, `PATHFINDER_VM_IMAGE_ID`, `PATHFINDER_VM_ROLE_ARN`
  - 유지: `PATHFINDER_S3_REGION`, `PATHFINDER_S3_BUCKET`, `ANTHROPIC_MODEL`, `PATHFINDER_CORS_ORIGINS`
  - 신규: `PATHFINDER_RULES_DIR`(기본 `<repo>/files/aiplc-rules`),
    `PATHFINDER_WORKSPACES_DIR`(기본 시스템 tmp 하위 — 프로젝트별 로컬 워크스페이스 루트)
  - `PATHFINDER_S3_BUCKET` 미설정 시: 기존처럼 목록 영속화 생략 + sync/restore 생략
    (로컬 디렉토리만으로 동작). 세션은 `FileSessionManager` 폴백 — 단, 저장 경로는
    VM 고정 경로(`/workspace/.sessions`)가 아니라 해당 프로젝트의 로컬 워크스페이스
    하위(`<local_root>/.sessions`)로 조정한다. AWS 없이 백엔드를 띄울 수는 있으나
    턴에는 Bedrock 자격증명이 필요하다.
- **README/.env.example**: 모드 A/B 구분 삭제 → 단일 실행 방법(백엔드+프론트).
  Python 3.11 요구는 백엔드로 통합(strands 의존성이 백엔드 pyproject로 합류).

## §5 에러·동시성 계약 (유지)

- 턴 중복: "turn already in progress" 이벤트 (무변경)
- pending 질문 없음: "no pending questions" (무변경)
- 에이전트 실패: sanitized "agent turn failed" + 서버측 로그 (드라이버 기존 로직)
- pending 프로브 실패: None 강등, 500 금지 (무변경)
- done 전 sync 실패: 터미널 이벤트 전 예외 표면화 — fail-closed (무변경)
- 프로젝트 삭제: `sandbox.stop()`(VM 정지) → `runner.stop()`(로컬 디렉토리 제거)로 대체.
  S3 삭제·멱등성·레지스트리 제거 순서는 projects.py 기존 로직 유지. delete-during-boot
  레이스 처리는 "부팅"이 mkdir 수준으로 가벼워져 대폭 단순해지나, attach 전 삭제 확인
  분기는 유지한다(ensure_workspace의 404 시맨틱 보존).

## 마이그레이션·호환성

- API 표면(라우트 경로·요청/응답 형태·SSE 이벤트 계약) 무변경 — 프론트 코드 수정 없음
  (e2e 단언 조정 제외).
- S3 레이아웃(projects/{pid}/..., sessions/...) 무변경 — 기존 프로젝트·세션 데이터가
  새 백엔드에서 그대로 이어진다. 배포된 MicroVM 스택은 `cdk deploy`(축소 스택)로 이미지·
  빌드롤이 제거되고 버킷은 유지된다.
- 롤백 경로: 없음(레거시 보존 안 함 — 사용자 결정). git 히스토리가 유일한 복구 수단.

## Open Questions

없음 — 본 설계 범위에서 미결정 사항은 모두 사용자 확인으로 해소됨.
