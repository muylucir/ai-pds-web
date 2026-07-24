# 프로토타입 생성 — MicroVM Claude Code 빌드 + EC2 호스팅 설계

날짜: 2026-07-24
상태: 설계 확정 (사용자 승인). 같은 날 드라이버 레이어 개정 승인 — CLI 직접
구동(`claude -p` + stream-json 파싱) 대신 **Claude Agent SDK(Python)** 사용,
및 그에 따른 1차 스코프 확장(턴 중단·구조화 질문·훅 기반 파일 추적).

## 1. 배경과 목표

Discovery 워크플로는 `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`
스펙 파일을 산출한다(prototype-context-generation 룰). 지금 Pathfinder는 이
스펙을 만드는 데서 끝나고, 실제 프로토타입 빌드는 사용자가 별도 코딩 에이전트
툴로 해야 한다. 이 기능은 그 갭을 메운다: **별도 코딩 에이전트 툴 없이
Pathfinder 안에서 프로토타입을 빌드·호스팅·검증한다.**

사용자 결정 사항:

- **빌드는 Tokyo Lambda MicroVM 안의 Claude Code** — 격리된 빌드 환경.
  이전 Pathfinder의 하네스 패턴(git 히스토리 `510fc66^`의 `harness/`)을 부활.
- **에이전트 구동은 Claude Agent SDK(Python)** — CLI 바이너리 직접 구동
  (`claude -p --continue` + stream-json 파싱, 과거 `claude_driver.py`) 방식과
  비교 후 SDK 채택. 판단 기준은 제어력·확장성(사용자 확인): 턴 중단
  (`client.interrupt()`), 구조화 질문(내장 `AskUserQuestion` 가로채기),
  in-process 훅(`PreToolUse`/`PostToolUse`)이 CLI `-p` 모드에서는 전부
  불가하거나 우회가 필요. 부수 이점: SDK wheel이 네이티브 Claude Code
  바이너리를 번들(2026-07 기준 공식 문서 확인)하므로 npm 글로벌 설치가
  사라지고 버전 핀이 requirements.txt 한 곳으로 모이며, stream-json 파싱·
  stderr drain·`--continue` 상태 관리 같은 저수준 코드를 SDK가 흡수.
  리스크는 SDK 버전 churn(0.2.x) — 정확 버전 핀(`==`)으로 관리(CLI 방식도
  버전 핀은 어차피 필요하므로 상쇄).
- **인증은 Bedrock** (`CLAUDE_CODE_USE_BEDROCK=1`) — SDK 공식 지원 경로.
  VM 실행 롤에 Bedrock 권한. Anthropic API 키 불필요. (aws-samples의
  managed-agents 웹훅 패턴은 org API 키 필요 + 인프라 과다로 검토 후 기각.)
- **대화형 UX** — 캔버스처럼 채팅으로 진행. SDK 메시지 객체 →
  AgentEvent → SSE 중계. 여기에 **구조화 질문 위저드**(Discovery 캔버스의
  질문 폼 UX 재사용)와 **턴 중단 버튼**을 1차 스코프에 포함(사용자 확인 —
  초기 "순수 채팅" 결정을 SDK 채택으로 개정).
- **UI는 별도 "프로토타입" 탭** — Discovery 캔버스와 분리.
- **결과물은 둘 다**: 코드 번들은 S3에 영속화 + 라이브 프리뷰 URL 제공.
- **호스팅은 Pathfinder EC2에서** — MicroVM은 빌드에만 쓰고 호스팅에는 쓰지
  않는다(호스팅 목적에 부적합). 프리뷰는 EC2 서브프로세스.
- **프리뷰 라우팅은 경로 기반 프록시** — `/api/proto/{pid}/{slug}/*` →
  로컬 포트. nginx/CloudFront 변경 없음.
- **VM 수명: 세션 동안 유지, 종료 시 정리** — suspend/resume 없음. 유휴 30분
  또는 사용자 종료 시 S3 sync 후 stop.
- **호스팅 운영 범위: 프로젝트당 소수(최대 3), 수동 종료** — systemd 등록
  없음, 백엔드 재시작 시 수동 재기동.

## 2. 아키텍처 개요

```
프론트 [프로토타입 탭]
   │ SSE (기존 AgentEvent 계약 재사용)
백엔드 FastAPI (EC2, 서울)
   ├─ PrototypeSession ──HTTP──▶ MicroVM (Tokyo, lambda-microvms)
   │     · VM 부팅/종료             ├─ harness 서버 (8080 app + 9000 hooks)
   │     · 파일 push/pull           └─ ClaudeSDKClient (claude-agent-sdk,
   │     · S3 sync (백엔드 중개)         CLAUDE_CODE_USE_BEDROCK=1)
   └─ ProtoHost ──▶ EC2 로컬 서브프로세스 (port 4001+)
         · S3 번들 다운로드 → npm install/build/start
         · /api/proto/{pid}/{slug}/* 리버스 프록시
```

- 공개 프리뷰 URL은 `https://<cf-domain>/api/proto/{pid}/{slug}/` — 기존
  nginx `/api → :8000` 라우팅을 그대로 타므로 호스팅 스택 변경이 없다.
- VM은 빌드 전용. 브라우저 트래픽은 VM에 절대 가지 않는다.

## 3. 컴포넌트

### 백엔드 (신규)

| 모듈 | 역할 |
|---|---|
| `pathfinder/proto/vm.py` | `LambdaMicroVMController` 부활(boot/stop/status + 하네스 토큰 민팅). suspend/resume 메서드는 부활하지 않음 |
| `pathfinder/proto/session.py` | `PrototypeSession` — 프로토타입 1개의 빌드 세션. VM 부팅 → PROTOTYPE-*.md·룰 push → 하네스 턴 중계 → 유휴 타이머(30분) → 종료 시 S3 sync + VM stop |
| `pathfinder/proto/host.py` | `ProtoHost` — S3 번들 다운로드 → `npm install`/빌드/기동(포트 4001+ 순차 스캔) → 서브프로세스 start/stop/status/log tail |
| `pathfinder/routes/prototypes.py` | REST + SSE: 세션 시작/종료/중단, 메시지·답변 스트림, 호스팅 start/stop/status, `/proto/{pid}/{slug}/{path:path}` 스트리밍 리버스 프록시(httpx) |

### 하네스 (부활, `harness/` 디렉토리)

git 히스토리에서 서버 골격은 부활, 드라이버는 신규 작성:

- `serve.py` — 앱 서버(8080) + hooks 서버(9000) 이중 스레드(블로킹 헬스체크
  때문에 필수 — 과거 주석 참조). 부활
- `app.py` — 턴 HTTP API (message/answers/pending/files). 부활 + `/interrupt`
  라우트 추가
- `sdk_driver.py` — **신규**(과거 `claude_driver.py`를 부활하지 않고 대체).
  빌드 세션당 `ClaudeSDKClient` 1개 유지 — 클라이언트 인스턴스가 멀티턴
  컨텍스트를 유지하므로 `--continue` 프로세스 상태 플래그 불필요. 옵션:
  `permission_mode="bypassPermissions"`, `cwd="/workspace"`,
  `env={"CLAUDE_CODE_USE_BEDROCK": "1", ...}`, `AskUserQuestion` allowed.
  이벤트 번역은 타입 객체 매핑: `TextBlock`→`message`,
  `ToolUseBlock`→`status`(도구명, 기존 dedupe 패턴), `ResultMessage`→`done`.
  파일 변경 감지는 stream-json tool_use 파싱 대신 PostToolUse 훅(아래)이
  담당. 테스트 주입점으로 client factory를 받는다(현 StrandsDriver의
  `agent_factory` 패턴과 동일 — fake SDK client로 AWS 없이 검증)
- **SDK 훅** (`sdk_driver.py` 내):
  - `PostToolUse`(matcher `Write|Edit|MultiEdit`) → `file_changed` 이벤트
    방출. 워크스페이스 경로 이스케이프 가드(과거 `_rel`의 `..` 거부 로직)를
    훅 안으로 이식
  - `PreToolUse` 거부(가드레일)는 1차 제외 — VM 격리 + 최소 권한 롤로 충분.
    확장 포인트로만 기록
- `hooks.py` — `/ready`·`/health`. 부활. `claude_cli_diagnostic`은 SDK
  import + 번들 바이너리 실행 확인으로 교체(기존 방침대로 로그만, 빌드
  게이트 아님 — 과거 /ready 503-루프 학습 사항). 번들 바이너리의
  al2023/아키텍처 호환은 이미지 첫 빌드에서 이 진단으로 확인
- Dockerfile — `pip install`로 harness 의존성 + `claude-agent-sdk`
  (정확 버전 핀 `==`, CLI 바이너리 번들). `npm install -g
  @anthropic-ai/claude-code` 불필요. Node.js/npm은 프로토타입 빌드용으로
  유지. **non-root `harness` 유저 유지 필수** — SDK도 동일 바이너리를
  스폰하므로 root에서 `bypassPermissions` 거부(과거 6d21e1f 학습 사항)가
  그대로 적용

**부활하지 않는 것**: `claude_driver.py`(stream-json 파싱·stderr drain —
SDK가 흡수), `strands_driver.py`, `aiplc_tools.py`(이 VM의 에이전트는
Claude Code 자체 — 내장 bash/file 도구 사용), suspend/resume 제어.

### 프론트엔드

- `app/projects/[id]/prototypes/page.tsx` — 프로토타입 탭. PROTOTYPE-*.md
  목록(기존 artifacts API) + 프로토타입별 카드
- 카드 상태: `없음`(스펙만 존재) → `빌드중`(세션 활성) → `built`(S3 번들
  존재) → `실행중`(호스팅 활성) / `실패`
- `components/prototypes/` — 빌드 채팅 패널(캔버스 컴포넌트 변형), 상태 카드,
  프리뷰 링크·호스팅 로그 뷰어
- 채팅 패널 추가 요소: **중단 버튼**(턴 진행 중 노출 → 세션 중단 API),
  **질문 폼**(`questions` 이벤트 수신 시 Discovery 캔버스의 질문 위저드
  컴포넌트 재사용 — 동일 payload 계약), 파일 변경 목록(`file_changed`
  이벤트 누적)

### 인프라 (CDK)

- drill 스택: MicroVM 이미지(al2023 베이스 + 하네스 asset)·이미지 빌드 롤·VM
  실행 롤·로그 그룹 부활(과거 정의 재사용). VM 실행 롤에
  `bedrock:InvokeModel*` 추가(글로벌 추론 프로파일 + 리전 와일드카드 ARN)
- 백엔드 롤: `lambda-microvms` Run/Get/Stop/토큰 민팅 권한 추가
- 호스팅 스택: 변경 없음

## 4. 데이터 흐름

### 빌드 세션 시작 (`POST /projects/{pid}/prototypes/{slug}/session`)

1. S3 워크스페이스에서 `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`
   존재 확인 (없으면 404)
2. Tokyo VM 부팅 → running 폴링(상한 90초) → 하네스 토큰 민팅
3. VM에 파일 push: PROTOTYPE-*.md + `prototype-building.md` 룰 상세.
   재빌드면 S3 번들도 복원
4. 첫 턴 자동 발화: PROTOTYPE-{slug}.md를 읽고 빌드하라 / 질문은 사용자에게 /
   완성물은 `/workspace/prototype/` 아래 + 빌드·실행 방법 README /
   **경로 프록시 하위에서 동작하도록 basePath·상대 경로 지원** /
   **LLM 호출은 Bedrock + 기본 자격증명 체인(API 키 하드코딩 금지), 리전·모델은
   환경변수로 수용**
5. SSE로 AgentEvent 스트림 → 채팅 패널 렌더

### 대화 턴

- 사용자 메시지 → `ClaudeSDKClient.query()` → 메시지 객체를 AgentEvent
  (`message`/`status`/`file_changed`/`done`/`error`)로 번역 → SSE 중계
- 파일 변경은 PostToolUse 훅에서 `file_changed`로 방출(경로 가드 포함)

### 구조화 질문 (AskUserQuestion 가로채기)

- 에이전트가 `AskUserQuestion` 도구를 호출하면 SDK 도구 콜백에서 가로챈다.
  **하네스가** 도구 입력(질문·선택지 스키마)을 기존 `questions` AgentEvent
  계약(interrupt_id + questions_payload)으로 번역해 방출 → 프론트 질문
  위저드는 무수정 재사용. 답변 역방향도 하네스가 answers 맵 → 도구 결과
  형태로 번역
- **Strands 인터럽트와의 차이**: 턴이 끝나지 않고 도구 콜백이 pending
  future로 **열린 채 대기**한다. 백엔드는 SSE 스트림을 유지하고, 사용자가
  기존 answers 경로로 답하면 future를 resolve → 같은 턴이 이어서 진행
- 질문 대기 중 유휴 30분 도달 시 VM stop으로 질문 소멸 — 데모 규모에서
  수용, 재시작은 새 세션 복구(기존 에러 모델과 동일)

### 턴 중단 (사용자 중단 버튼)

- 프론트 중단 버튼 → 백엔드 `POST .../session/interrupt` → 하네스
  `POST /interrupt` → `client.interrupt()`
- SDK 주의사항 반영: interrupt 후 버퍼에 남은 메시지(`ResultMessage
  subtype="error_during_execution"` 포함)를 **드레인 완료 후** 다음 턴 허용.
  드레인 중 이벤트는 그대로 SSE로 흘리고 마지막에 `status: "interrupted"` +
  `done`. 중복 interrupt 요청은 멱등 처리

### 세션 종료 (사용자 "완료" 버튼 또는 유휴 30분)

1. VM의 `/workspace/prototype/` pull → 해당 프로젝트의 S3 워크스페이스 프리픽스
   하위 `prototypes/{slug}/bundle/`에 업로드 (`node_modules`·`.next` 등 빌드
   산출물 제외)
2. VM stop, 세션 메타(`status`, 종료 시각) S3 기록

### 호스팅 (`POST /projects/{pid}/prototypes/{slug}/host`)

1. S3 번들 → EC2 로컬 `~/pathfinder-protos/{pid}/{slug}/`
2. `npm install` → README/package.json 스크립트로 빌드·기동. 환경변수는
   `PORT`(4001+ 빈 포트 스캔), `AWS_REGION`, `ANTHROPIC_MODEL` 최소만 전달
3. 상태 API 폴링 → 카드에 실행중 + 프리뷰 URL
4. `/api/proto/{pid}/{slug}/*` → `http://127.0.0.1:<port>/*` 스트리밍 프록시

### 재빌드/수정

빌드 완료 후 세션을 다시 시작하면 S3 번들을 VM에 복원하고 **새
`ClaudeSDKClient`**(이전 세션 resume 없음)로 시작 — 수정 요청을 첫 발화에
포함.

## 5. 보안

- **VM 실행 롤**: Bedrock invoke + CloudWatch 로그만. S3 접근 없음(파일은
  백엔드가 중개) — 생성 코드가 VM 안에서 뭘 하든 계정 리소스에 접근 불가
- **하네스 인증**: 민팅 토큰(과거 패턴). 에이전트 에러 상세는 서버 로그만
  — SSE에는 sanitize된 `error` 이벤트만(자격증명 노출 방지, 기존 계약 동일)
- **프로토타입 프로세스의 인스턴스 롤 공유(명시적 트레이드오프)**: EC2에서
  도는 프로토타입은 IMDS로 인스턴스 롤 자격증명을 얻을 수 있다. Bedrock
  호출은 이 경로로 동작(추가 인프라 불필요). 대신 인스턴스 롤의 다른
  권한(Pathfinder S3 버킷 등)에도 기술적으로 접근 가능 — 프로토타입 코드는
  우리 Claude Code가 생성하고 워크숍 데모용 임시 실행이므로 수용. 엄격한
  격리(별도 유저 + IMDS 차단 + 스코프 자격증명)는 데모 규모에 과함
- **프리뷰 접근 제어**: CloudFront 경유 + X-Origin-Verify는 기존 그대로.
  프로토타입별 추가 인증 없음(데모 규모 수용)

## 6. 에러 처리

| 상황 | 처리 |
|---|---|
| VM 부팅 실패/타임아웃(90초) | 세션 시작 API 502 + 사유. 카드 "시작 실패 — 재시도" |
| 턴 중 SDK/에이전트 프로세스 에러 | sanitize된 `error` 이벤트(상세는 로그만). 세션 유지, 재시도 가능 |
| 턴 중 VM 죽음/네트워크 단절 | SSE `error` 후 세션 `failed`. 재시작 = 새 VM + S3 복원 |
| 사용자 중단 | `client.interrupt()` → 버퍼 드레인 → `status: "interrupted"` + `done`. 세션 유지 |
| 질문 대기 중 유휴 만료 | pending future 소멸 + S3 sync + VM stop (질문은 유실 — 수용) |
| 유휴 30분 | S3 sync → VM stop → 카드 `built` 복귀. 진행 중 턴은 타이머 리셋 |
| 백엔드 재시작 | 인메모리 세션 소멸. 기동 시 계정 내 pathfinder 태그 VM 조회·stop(고아 정리). 호스팅 프로세스도 소멸 — 수동 재기동 |
| npm install/빌드 실패 | 로그 tail을 상태 API로 노출, 카드 "빌드 실패 — 로그 보기". 빌드 세션 재개로 수정 유도 |
| 포트 충돌 | 4001부터 순차 스캔, 상태 API가 실제 포트 반환 |
| 프록시 대상 다운 | `/api/proto/...` 502 + 안내 페이지 |

## 7. 테스트

- **백엔드 단위**: fake boto3 client로 VM 컨트롤러(과거 테스트 부활),
  fake 하네스(httpx.ASGITransport)로 `PrototypeSession`, 더미 npm 프로젝트
  fixture로 `ProtoHost`(start/stop/log/포트 스캔), 로컬 임시 HTTP 서버로
  프록시 스트리밍
- **하네스 단위**: fake SDK client 주입(client factory seam)으로 메시지 객체
  → AgentEvent 번역, interrupt 후 버퍼 드레인, AskUserQuestion 콜백 →
  questions 이벤트 → answers resolve 왕복, PostToolUse 경로 가드
- **프론트 단위**: 카드 상태 전이, 채팅 패널 SSE 렌더(기존 캔버스 패턴),
  질문 위저드 재사용 렌더, 중단 버튼 상태
- **인프라**: drill 스택에 MicroVM 이미지·롤·Bedrock 정책 assertion 복원
- **e2e**: 실 VM·실 Bedrock 필요 — Playwright 제외, 수동 체크리스트 문서화
  (기존 방침 동일)

## 8. 스코프 제외

- 프로토타입별 추가 인증·HTTPS 서브도메인 (경로 프록시로 충분)
- `PreToolUse` 가드레일 훅(도구 호출 조건부 거부) — VM 격리 + 최소 권한
  롤로 충분. SDK 채택으로 열리는 확장 포인트로만 기록
- VM suspend/resume (세션 단위 부팅·정리로 충분)
- 호스팅 프로세스의 systemd 상시화·자동 TTL (수동 종료)
- Node.js 외 런타임(파이썬 백엔드 프로토타입 등)은 1차 스코프 제외 —
  PROTOTYPE 빌드 지침이 Node/Next 스택을 기본으로 명시
- Discovery 캔버스와의 세션 연동(프로토타입 탭은 독립 세션)
