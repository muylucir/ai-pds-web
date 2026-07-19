# Pathfinder — Strands 엔진 전환 + 분할 워크스페이스 UI 설계

**날짜**: 2026-07-19
**상태**: 승인됨
**대상 독자**: 구현 담당 개발자
**선행 문서**: `2026-07-17-pathfinder-web-service-design.md` (기존 아키텍처 — 본 문서가 §2 "파일이 곧 계약"과 §5 화면 구성을 대체한다)

## 1. 배경과 목표

현행 구조는 MicroVM 안에서 Claude Code(headless)를 서브프로세스로 돌리고, 산출 파일(`*-questions.md` 등)을 백엔드가 파싱해 UI를 만든다. 실전 운용에서 두 가지 문제가 확인됐다:

1. **제어 어려움** — Claude Code는 블랙박스다. 스테이지 전이·질문 생성 시점을 우리가 제어할 수 없고, stream-json 번역과 파일 파싱이라는 두 겹의 간접층을 거쳐야 UI가 반응한다.
2. **컨텍스트 휘발** — `--continue`는 VM 로컬 세션 파일에 의존한다. MicroVM 만료(최대 8시간)·장애 시 대화 컨텍스트가 사라지고, 파일 기반 session-continuity 룰로 "다시 읽어서" 복구하는 우회만 가능하다.

**결정**: 에이전트 실행 엔진을 Claude Code에서 **Strands Agents SDK(Python)** 로 재개발한다. MicroVM 격리·기존 하네스 HTTP 계약·CDK 인프라는 유지하고, 하네스의 드라이버 계층만 교체한다. 대화 컨텍스트는 Strands `S3SessionManager`로 S3에 영속화해 VM 수명과 분리한다. UI는 탭 전환(질문 답변 ↔ 캔버스) 대신 **3분할 워크스페이스 단일 화면**으로 개편한다.

방법론 원칙은 유지한다: aiplc-rules 마크다운은 코드로 재작성하지 않고 프롬프트 데이터로 주입한다. 룰은 재배포 없이 진화한다.

## 2. 아키텍처 (승인안 A — 하네스 드라이버 교체)

```
사용자 브라우저
   │  3분할 워크스페이스 (좌: 진행단계 / 중앙: 채팅 / 우: 컨텍스트 패널)
FastAPI 백엔드 (thin BFF)
   │  SSE 중계 · 세션/프로젝트 관리 · 이벤트 소비 (파싱 아님)
Lambda MicroVM (프로젝트당 1개, 도쿄)
   ├─ 하네스 HTTP 서버 (기존: POST /message→SSE, /files, /health, hooks 9000)
   ├─ strands_driver  ← claude_driver 교체 지점
   │    └─ Strands Agent (BedrockModel, IAM 롤 인증)
   │         ├─ system_prompt: core-workflow.md + common 룰
   │         ├─ tools: file_read/file_write + ask_questions/report_stage/submit_document
   │         └─ S3SessionManager(session_id=project_id) → 대화+interrupt 상태 S3 영속화
   └─ 워크스페이스: aiplc-docs/ (계속 생성 — 기록·핸드오프·감사용)
S3: 세션 상태(신규 prefix) + 아티팩트(기존)  /  DynamoDB·ECR·기타: 기존 설계 유지
```

**유지되는 것** — MicroVM 이미지 파이프라인(CDK·package-harness), 하네스 듀얼서버(app 8080 / hooks 9000)와 라이프사이클 훅, `/files` 경로 가드, 턴 종료 후 VM→S3 아티팩트 동기화, 백엔드 `Sandbox` 추상화와 local/microvm 이원화, 크리덴셜 레다크션.

**교체되는 것** — `harness/claude_driver.py`(Claude Code 서브프로세스 + stream-json 번역) → `harness/strands_driver.py`(Strands 에이전트 인프로세스 실행). MicroVM 이미지에서 Claude Code 설치 제거, `strands-agents`·`strands-agents-tools` 설치.

**컨텍스트 휘발 해결** — `S3SessionManager(session_id=project_id, bucket=PATHFINDER_S3_BUCKET, prefix="sessions/")`가 메시지 히스토리·에이전트 상태·interrupt 상태를 S3에 저장한다. VM이 죽고 새 VM이 떠도 같은 session_id로 Agent를 만들면 대화가 그대로 복원된다. 파일 재독(session-continuity 룰)은 보조 수단으로 강등된다.

### 왜 B(그린필드)·C(AgentCore)가 아닌가

- **B. VM 안 그린필드 재작성**: 계약은 깔끔해지지만 검증된 하네스 코드(라이프사이클 훅, 경로 보안, 듀얼서버, S3 sync)와 배포 파이프라인을 다시 검증해야 한다. 드라이버 교체만으로 목적(제어·영속화)을 전부 달성한다.
- **C. AgentCore Runtime 이전**: 관리형이지만 "MicroVM은 계속 사용" 방침과 충돌하고 기존 인프라를 폐기하게 된다. 프로토타입 빌드(셸·로컬 dev 서버)는 어차피 샌드박스 VM이 필요하다.

## 3. 에이전트 설계 — "코드가 접점을 강제, 룰이 내용을 운전"

단일 Strands Agent. 스테이지 오케스트레이션을 Python 그래프로 강제하지 않는다(방법론의 "workflow adapts to the work" 원칙 유지). 대신 **UI 접점만 도구로 강제**한다:

| 도구 | 역할 | 동작 |
|---|---|---|
| `ask_questions` | 질문 위저드 | 구조화 입력(질문 리스트: id·본문·옵션 A–F·추천★·Other 허용). 호출 즉시 `questions` 이벤트 발행 → **`ToolContext.interrupt()`로 사용자 답변 대기**. 재개 시 답변 JSON이 tool result로 들어감 |
| `report_stage` | 진행 단계 사이드바 | 스테이지 전이 선언(stage id·상태·요약) → `stage` 이벤트 발행. aiplc-state.md 기록은 별도로 계속 |
| `submit_document` | 문서 리뷰 | discovery-document 준비/갱신 선언(경로·버전·요약) → `document` 이벤트 발행. 승인/수정 요청은 다음 user 턴으로 전달 |
| `file_read`/`file_write` | 워크스페이스 파일 | strands_tools 제공. aiplc-docs/ 산출물 생성 — 룰이 요구하는 파일 계약 그대로 |

- **system_prompt** = `core-workflow.md` + `common/*` 룰 결합 + 접점 규약 추가문("질문은 반드시 ask_questions 도구로, 파일로만 남기지 말 것" 등). 스테이지별 상세 룰(`discovery/*.md`)은 룰 원문의 지시대로 에이전트가 `file_read`로 로드한다 — 컨텍스트 절약 구조 유지.
- **모델**: `BedrockModel(model_id=$ANTHROPIC_MODEL)` — 기존 env 계약 유지(Bedrock cross-region inference profile, 예: `global.anthropic.claude-sonnet-5`). 인증은 VM 실행 롤(IAM) — boto3 기본 크리덴셜 체인. 장기 키 없음 원칙 유지.
- **interrupt 영속화**: `ask_questions`가 interrupt로 멈춘 상태는 S3SessionManager가 함께 저장한다. VM이 죽어도 새 VM에서 같은 세션으로 복원하면 "질문 대기 중" 상태 그대로 재개된다. 답변 제출 = `[{"interruptResponse": {"interruptId": ..., "response": {답변 JSON}}}]` 메시지로 재개.
- **파일은 기록, 이벤트가 계약**: `aiplc-docs/`(questions.md 포함) 파일은 룰 호환·핸드오프·감사 추적을 위해 계속 생성된다. 단 UI는 파일을 파싱하지 않는다.

## 4. 이벤트 계약 (AgentEvent 확장)

`kind` 확장 — 백엔드(`sandbox/base.py`)와 하네스(`strands_driver.py`)의 미러 모델 동기 유지:

```
kind: message | questions | stage | document | file_changed | status | done | error
payload: str | None   # questions/stage/document의 구조화 JSON (직렬화)
```

| kind | 발생원 | 페이로드 |
|---|---|---|
| `message` | 텍스트 델타/완성 텍스트 | text |
| `questions` | ask_questions 도구 | 질문 리스트 JSON + interrupt_id |
| `stage` | report_stage 도구 | {stage, status, summary} |
| `document` | submit_document 도구 | {path, version, summary} |
| `file_changed` | file_write 관찰 | path (기존 유지) |
| `status`/`done`/`error` | 루프 상태 | 기존 유지 |

- **스트리밍**: strands_driver가 `agent.stream_async()` 이벤트를 AgentEvent로 번역한다 — `data`(텍스트 델타)→`message`, tool 호출→해당 구조화 이벤트, `result`→`done`. interrupt 발생 시 `questions` 이벤트 후 스트림을 정상 종료(`done`)하고 하네스는 "answer 대기" 상태를 유지한다.
- **하네스 HTTP 계약 유지 + 2 엔드포인트 추가**: `POST /message`(자유 텍스트 턴), `POST /answers`(구조화 답변 → interrupt 재개) — 둘 다 SSE 응답. `GET /pending` — 대기 중 interrupt가 있으면 그 `questions` 페이로드(+interrupt_id)를, 없으면 null을 반환. UI가 페이지 로드/VM 복구 후 대기 질문을 복원하는 유일한 경로다(SSE는 턴 단위라 리플레이가 없다). `/files`·`/health`·hooks는 무변경.
- **백엔드**: `turns.py`에 `/projects/{pid}/answers`(같은 SSE 중계 패턴)와 `/projects/{pid}/pending`(단건 조회 중계) 추가. `Sandbox`에 `send_answers()` 추가 — LocalSandbox는 스크립트된 가짜 questions/stage/document 이벤트를 내도록 확장(UI 개발이 AWS 없이 가능해야 함). 기존 questions.md 파서·routes/answers.py의 파일 기입 경로는 **microvm 모드에서 이벤트 소비로 대체**되고, 파서 자체는 local 모드 픽스처·폴백용으로 유지한다.
- **폴백**: 에이전트가 도구를 안 쓰고 질문을 텍스트로만 내보내는 이탈 케이스 → UI는 채팅 메시지로 그대로 표시하고 자유 텍스트 입력으로 진행 가능(진행이 막히지 않는다는 기존 원칙 유지).

## 5. UI — 3분할 워크스페이스

신규 화면 `/projects/[id]/workspace`가 **질문 답변 탭 + 빌드 캔버스 탭을 대체**한다. 메뉴: **대시보드 | 워크스페이스 | 문서 리뷰**.

그리드 비율은 **1 : 4.5 : 4.5** (진행 단계 사이드바 : 채팅 : 컨텍스트 패널) — 좌 사이드바는 좁은 고정폭 축(스테이지 리스트만), 채팅과 컨텍스트 패널이 나머지를 균등 분할한다.

```
┌────────────────────────────────────────────────────────┐
│ 헤더: 대시보드 | 워크스페이스 | 문서 리뷰               │
├──────────┬───────────────────┬─────────────────────────┤
│ 진행 단계 │ 채팅               │ 컨텍스트 패널            │
│ 사이드바  │ (항상 표시)        │ (단계 따라 전환)         │
│          │                   │                         │
│ ● 모드선택│ AI 메시지 스트림    │ questions 이벤트         │
│ ● Envision│ 사용자 메시지      │  → 질문 폼 (라디오·★·    │
│ ○ 분석    │ 상태/도구 라인     │     Other·일괄 제출)     │
│ ○ 전략    │                   │ 프로토타입 단계          │
│ ○ GTM    │ ┌───────────────┐ │  → PreviewPanel(iframe)  │
│          │ │ 메시지 입력    │ │ 평소                     │
│          │ └───────────────┘ │  → 최근 산출물·활동 요약   │
└──────────┴───────────────────┴─────────────────────────┘
```

- **좌 (진행 단계)**: `stage` 이벤트 구독. 기존 `CanvasSidebar` 개조. 스테이지 완료/진행/대기 상태 표시.
- **중앙 (채팅)**: `ChatTimeline`·`ChatInput`·`AiMessage` 재사용. `message`/`status` 이벤트 렌더. 질문이 도착하면 채팅에는 요약 카드(`QuestionSummaryCard` 재사용, "우측 패널에서 답변" 안내)만 표시.
- **우 (컨텍스트 패널)**: 상태 머신 — `questions` 수신 → 질문 폼 모드(`QuestionForm`·`QuestionCard` 재사용, 제출 → `POST /answers`); 프로토타입 단계(`stage`로 판별) → `PreviewPanel`; 그 외 → 최근 산출물(`file_changed` 누적)·활동 요약.
- **유지 화면**: 대시보드 탭(타임라인·활동 피드 — audit.md 기반 유지), 문서 리뷰 탭(`document` 이벤트로 갱신 알림 배지 추가), 프로젝트 목록.
- **제거 화면**: `/questions`, `/canvas` 라우트(컴포넌트는 워크스페이스로 이동/재사용). 기존 라우트는 `/workspace`로 리다이렉트.
- **반응형**: 좁은 화면(<lg)에서 좌 사이드바는 햄버거/드로어, 우 패널은 하단 시트 또는 채팅 위 탭 전환. 질문 도착 시 시트에 배지 표시.

## 6. 수명 주기 (변경분)

- **정상 턴**: 브라우저 → 백엔드 SSE → 하네스 → strands_driver → Bedrock. 턴 종료마다 기존 VM→S3 아티팩트 동기화 + S3SessionManager 자동 세션 저장.
- **VM 만료/장애 복구**: 새 VM 부팅 → S3에서 워크스페이스 복원(기존) + 같은 session_id로 Agent 생성 → **대화·interrupt 상태 자동 복원**. 진행 중이던 질문 폼도 프론트가 `GET /pending`으로 대기 질문을 조회해 그대로 이어진다.
- **suspend/resume**: 기존과 동일 — 인메모리 에이전트가 스냅샷에 포함되므로 무변경 재개. S3 세션은 그 위의 안전망.

## 7. 테스트 전략 (변경분)

- **strands_driver 유닛**: Strands Agent를 스텁 모델/스텁 도구 호출로 구동해 stream_async 이벤트→AgentEvent 번역, interrupt→questions 이벤트→재개 왕복을 검증. 실 Bedrock 불필요.
- **하네스 유닛**: `/answers` 엔드포인트 추가분 + 기존 테스트 유지.
- **백엔드 유닛**: LocalSandbox의 구조화 이벤트 시나리오(질문→답변→스테이지 전이) + `/answers` 라우트.
- **프론트 유닛/e2e**: 워크스페이스 3분할 렌더·질문 폼 왕복·반응형 접힘. 기존 골든 패스 리플레이(pilot1 audit 41엔트리)는 이벤트 계약 버전으로 이식 — 답변 시퀀스를 `/answers`로 주입해 stage 전이 일치 검증.

## 8. 구현 순서

1. **엔진**: strands_driver + 도구 3종 + S3SessionManager + 하네스 `/answers` + 이미지 교체(CDK 재배포) — claude_driver와 병렬 존재시키고 env 플래그로 전환, 검증 후 제거.
2. **백엔드**: AgentEvent 확장, `/answers` 중계, LocalSandbox 구조화 시나리오.
3. **UI**: 워크스페이스 화면 + 라우트 정리 + 반응형.
4. **골든 패스 이식** + 실 VM 드릴(부팅→질문→답변→복구 리허설).

## 스코프 제외 (YAGNI)

- 멀티 에이전트(Graph/Swarm) — 단일 에이전트로 충분, 방법론도 단일 운전자 전제.
- DynamoDB 세션 매니저 커스텀 구현 — S3SessionManager로 충분(공식 DynamoDB 매니저 없음 확인).
- AgentCore Runtime/Memory 이전, 실시간 모순 감지, 룰 편집 UI, SSO(기존 제외 항목 유지).
- 대시보드 개편 — 기존 파일(audit.md) 기반 유지, 이벤트 기반 전환은 이후 과제.
