# Discovery 드라이버: Strands → Claude Agent SDK (Design)

2026-07-27.

## 목표

Discovery 워크플로우를 구동하는 드라이버를 Strands Agents SDK에서 Claude Agent
SDK로 이관한다. **목표는 룰을 더 잘 쓰는 구조를 발명하는 게 아니라, AI-PLC 룰이
전제한 실행 환경으로 되돌리는 것이다.**

## 왜

AI-PLC 룰은 Claude Code를 실행 환경으로 전제하고 쓰인 문서다. 상류
([aws-samples/sample-ai-plc](https://github.com/aws-samples/sample-ai-plc))의
셋업이 그것을 명시한다:

```
my-discovery-project/
├── CLAUDE.md                    ← core-workflow.md를 그대로 복사
├── aws-aiplc-rule-details/      ← 상세 룰, CWD 상대경로로 읽힘
└── aiplc-docs/                  ← 산출물 (자동 생성)
```

skills·서브에이전트·슬래시 커맨드·MCP를 쓰지 않는다. `CLAUDE.md`가 지시이고
상세 룰은 온디맨드 파일 읽기다. 우리 `core-workflow.md:18`도
`Rule details location: ./aws-aiplc-rule-details/`로 CWD 상대경로를 전제한다.

현재 Strands 구현은 **그 환경을 손으로 재현**하고 있다:

| 상류 전제 (Claude Code) | 현재 Strands 구현 |
|---|---|
| `CLAUDE.md`가 워크플로우 지시 | `_system_prompt()`가 룰 전문을 매 세션 시스템 프롬프트에 인라인 |
| `./aws-aiplc-rule-details/` CWD 상대 | `file_read`가 `aiplc-rules/` 프리픽스를 특수 처리 후 벗김 |
| 내장 Read / Write / Edit / Glob | `@tool`로 `file_read` / `file_write` / `file_append` 자작 |
| 내장 `AskUserQuestion` | `ask_questions` + interrupt + 스키마를 프롬프트로 지시 |

마지막 항목의 비용은 실측됐다: `is_other` 중복으로 보기 텍스트가 사라진 버그
(2026-07-26, `0c88fc3`). `AskUserQuestion`은 SDK가 스키마를 강제하므로 그 부류의
버그가 구조적으로 생기지 않는다.

### 검토했으나 채택하지 않은 것

**룰을 `SKILL.md`로 승격.** 상류가 의도적으로 CLAUDE.md + 온디맨드 읽기만 쓴다.
skills로 재구성하면 상류 업데이트를 받아올 수 없고(fork 유지 부담), 룰 본문의
파일 읽기 지시와 충돌한다. 컨텍스트 이득은 `_system_prompt()` 인라인이 없어지는
것만으로 이미 온다.

**`report_stage`/`submit_document`를 훅으로 대체.** 스테이지 전이는 모델이
명시적으로 선언해야 신뢰할 수 있다. `aiplc-state.md` 쓰기에서 역추론하면 한 턴에
여러 번 갱신될 때 UI가 흔들린다.

### 리스크로 오해했던 것 (해소)

"예전에 subprocess → in-process로 옮긴 것을 되돌리는 셈"이라 우려했으나 사실이
아니다. `docs/superpowers/plans/2026-07-21-microvm-removal-inprocess-agent.md`가
보여주듯 과거 전환은 **MicroVM 제거**였다 — 없앤 것은 VM 부팅·HTTP 중계·토큰
민팅 계층이고 in-process는 그 결과물이다. Claude Agent SDK의 subprocess는 로컬
CLI 실행이라 층위가 다르다. 같은 시기 `proto/builder.py`가 정확히 이 방향
(MicroVM 제거 + Claude Agent SDK subprocess)으로 가서 프로덕션에서 돌고 있다.

## 자산: 참조 구현이 이미 우리 코드에 있다

`proto/builder.py`가 같은 문제를 Claude Agent SDK로 이미 풀었다. 질문 왕복
(`AskUserQuestion` + `can_use_tool` 가로채기), 이벤트 번역(`_translate`), 파일
변경 감지(`PostToolUse` 훅), status 중복 제거, 질문 대기 중 스트림 공백 레이스
처리까지 전부 있다. 가짜 SDK(`tests/fakes/fake_sdk.py`)도 있다.

---

## 아키텍처

```
Frontend ◀── SSE ── AgentRunner ◀── AgentEvent ── driver_factory
                    (무변경)                        ├─ ClaudeDriver  (기본)
                                                   └─ StrandsDriver (폴백)
```

계약은 세 메서드다 — `run(text, session)`,
`run_answers(interrupt_id, answers, session)`, `pending(session)`.
`runner.py`가 이것만 쓰고(`:129,167,183`), `AgentEvent`(kind: message ·
questions · stage · document · file_changed · status · done · error)도 불변이므로
**`runner.py`와 라우트는 변경이 없다.**

### 계약은 두 층이다 — 타입은 안전하고 값은 아니다

두 SDK의 원시 출력은 전혀 다르다:

| | Strands | Claude Agent SDK |
|---|---|---|
| 스트림 단위 | dict (`{"data":...}`, `{"current_tool_use":...}`) | 객체 (`AssistantMessage`, `ResultMessage`) |
| 텍스트 | `ev["data"]` | `block.text` (TextBlock) |
| 도구 호출 | `ev["current_tool_use"]["name"]` | `block.name` (ToolUseBlock) |
| 종료 | `ev["result"]` | `ResultMessage` |

**타입 차이는 드라이버가 흡수한다.** `driver.py:186-196`이 dict를,
`builder.py:286-300`의 `_translate()`가 객체를 각각 `AgentEvent`로 번역한다.
프로토타입 빌드가 이미 Claude Agent SDK로 같은 질문 위저드를 구동하는 것이
실증이다.

**그러나 `AgentEvent.text`에 담기는 값(도구 이름)은 SDK마다 다르고, 프론트가 그
문자열을 키로 쓴다.** 그래서 프론트엔드 변경이 필요하다 — 2곳:

1. **`AiMessage.tsx:9-16` `ACTIVITY_LABELS`** — 도구명 → 한글 활동 문구 매핑.
   Strands 이름(`file_write`, `ask_questions`, …)만 있어서 Claude SDK 이름
   (`Write`, `AskUserQuestion`, …)이 오면 폴백(`:19`)이 발동해 사용자에게
   `Write 실행 중…` 같은 영어 도구명이 노출된다. 크래시는 아니지만 UX가 조용히
   나빠진다.
2. **`workspace/page.tsx:100`** — 첨부 파일 안내 프롬프트에
   `"필요 시 file_read로 읽으세요"`가 하드코딩. 모델에게 가는 지시문이므로 Claude
   SDK에서는 존재하지 않는 도구를 지목한다.

**해법(선택 B): 프론트 매핑에 새 이름을 추가하고 기존 키는 남긴다.**

```ts
const ACTIVITY_LABELS = {
  // Claude Agent SDK 내장 도구
  AskUserQuestion: "질문을 준비하고 있어요…",
  Write: "문서를 작성하고 있어요…",
  Edit: "문서를 작성하고 있어요…",
  MultiEdit: "문서를 작성하고 있어요…",
  Read: "자료를 확인하고 있어요…",
  Glob: "자료를 찾고 있어요…",
  // 양쪽 드라이버 공통 커스텀 도구
  report_stage: "진행 상황을 기록하고 있어요…",
  submit_document: "문서를 제출하고 있어요…",
  // Strands 드라이버 (env 폴백 기간 동안 유지)
  ask_questions: "질문을 준비하고 있어요…",
  file_write: "문서를 작성하고 있어요…",
  file_append: "문서를 작성하고 있어요…",
  file_read: "자료를 확인하고 있어요…",
};
```

첨부 안내 문구는 도구 이름을 언급하지 않는 표현으로 바꾼다(예: "필요 시 이
파일을 읽어보세요") — 두 드라이버 모두에서 맞고, 도구 이름 변경에 다시 깨지지
않는다.

**대안(A: 드라이버가 도구 이름을 정규화)은 채택하지 않는다.** `Write`→
`file_write`로 매핑하면 `AgentEvent.text`가 실제 호출된 도구와 달라진다 — 로그와
화면이 어긋나 디버깅을 어렵게 만든다. env 토글로 두 드라이버가 공존하는 기간에는
각자 자기 어휘를 쓰는 것이 정직하다.

전환은 `PATHFINDER_DISCOVERY_DRIVER=claude|strands`, 기본 `claude`. 워크숍 중
문제가 나면 env 하나로 되돌린다 — 다섯 번의 배포 사고를 겪은 만큼 탈출로를 둔다.
워크숍이 끝나면 `StrandsDriver`와 `strands-agents` 의존성을 삭제하는 별도 커밋.

### 파일 구조

```
backend/pathfinder/agent/
  driver.py            # StrandsDriver — 유지, 손대지 않음
  claude_driver.py     # ClaudeDriver ★신규
  workspace_rules.py   # 룰 배치 ★신규
  pending_store.py     # pending 질문 S3 영속 ★신규
  questions_payload.py # 기존 — 적용 지점 이동 + builder와 통합
  tools.py             # 6개 → 2개
discovery-config/
  CLAUDE.md            # Pathfinder 통합 규약 ★신규
```

`claude_driver.py`를 별 파일로 두는 이유: 두 드라이버가 공존해야 하고
`driver.py`가 이미 240행이라 한 파일에 두면 둘 다 읽기 어려워진다.

---

## 워크스페이스 구성

매 턴 시작 시 상류 레이아웃으로 만든다. `runner.py`의
`_restore_workspace_from_s3()` 직후에 룰 배치가 들어간다.

```
rule/aiplc-rules/  (읽기 전용 마스터)      워크스페이스/{project_id}/  (CWD)
  aws-aiplc-rules/core-workflow.md  ──▶  CLAUDE.md
  aws-aiplc-rule-details/           ──▶  aws-aiplc-rule-details/
                                          aiplc-docs/    ← S3에서 restore
                                          uploads/       ← S3에서 restore
```

`workspace_rules.py`가 이 복사를 담당한다. 룰은 읽기 전용이고 내용이 바뀌지
않으니 **이미 있고 크기가 같으면 건너뛴다** — 매 턴 수십 개 파일을 다시 쓰지
않는다.

`_sync_workspace_to_s3()`의 `_SYNC_GLOBS`가 `aiplc-docs/**`·`prototype/**`·
`uploads/**`로 한정되어 있어 `CLAUDE.md`와 `aws-aiplc-rule-details/`는 S3로
올라가지 않는다. 손댈 필요 없이 이미 안전하다.

### 룰은 config dir이 아니라 워크스페이스로 간다

`discovery-config/`는 프로세스가 공유하는 단일 설정 디렉터리다. 룰이
워크스페이스에 있어야 에이전트가
`./aws-aiplc-rule-details/common/process-overview.md`를 그대로 읽는다. 지금
`file_read`가 프리픽스를 벗기는 특수 처리가 바로 이 불일치를 메우는 우회이며,
**그 우회를 없애는 것이 이 이관의 목적**이다.

| 디렉터리 | 내용 | 스코프 |
|---|---|---|
| `rule/aiplc-rules/` | 상류 룰 **원본**. 상류 업데이트를 받는 곳 | 리포, 읽기 전용 |
| 워크스페이스 `{project_id}/` | `CLAUDE.md` + `aws-aiplc-rule-details/` 사본 + 산출물 | project (CWD) |
| `discovery-config/` | 우리 통합 규약 `CLAUDE.md`만 | user (config dir) |

---

## CLAUDE_CONFIG_DIR 분리

워크스페이스는 이미 나뉘어 있다(`app.py:250` Discovery vs `app.py:191`
프로토타입). 새로 나눠야 하는 것은 SDK의 설정·스킬 로딩 지점이다.

```
proto-config/        PATHFINDER_PROTO_CONFIG_DIR      (기존)
  CLAUDE.md          "한국어 / 프로토타입 디자인은 shadcn-design 스킬"
  skills/shadcn-design/
  agents/

discovery-config/    PATHFINDER_DISCOVERY_CONFIG_DIR  ★신규
  CLAUDE.md          Pathfinder 통합 규약
  (skills 없음 — 상류가 쓰지 않는다)
```

**공유하면 안 되는 이유는 확인된 사실이다.** `builder.py`가 `skills="all"`을
쓰므로 config dir의 모든 스킬이 활성화된다 — 공유하면 Discovery가
`shadcn-design`을 켠 채로 돈다. 역방향도 같다: Discovery의 통합 규약
(`report_stage` 호출, `submit_document` 순서)이 빌더에 들어가면 존재하지 않는
도구를 부르려 한다. `builder.py`의 기존 주석이 격리 자체의 근거를 이미 기록한다
— 미지정 시 호스트 유저의 `~/.claude`가 섞여 워크숍 결과가 호스트 설정에 따라
달라진다.

**Discovery 클라이언트 옵션:** `setting_sources=["user", "project"]`(project가
워크스페이스의 `CLAUDE.md`를 읽는 경로), **`skills` 미지정**,
`cwd=워크스페이스`, `permission_mode="bypassPermissions"`(워크숍은 무인 실행 —
`builder.py`의 `DEFAULT_PERMISSION_MODE` 주석과 같은 근거).

### 통합 규약 이관

`driver.py`의 `_CONTACT_ADDENDUM`이 `discovery-config/CLAUDE.md`로 간다. 내용은
대부분 유지하되 도구 이름이 바뀐다: `file_write`/`file_append` 지시 → 내장
Write/Edit, `ask_questions` 지시 → `AskUserQuestion`. "audit.md에 추가할 때는
`file_append`를 쓰라"는 경고는 Edit 기준으로 다시 쓴다.

상류 룰 파일에 우리 규약을 append하지 않는다 — 원본과 **바이트 동일**하게
유지되어 상류 업데이트를 그냥 덮어쓸 수 있다.

---

## 질문 왕복과 pending 영속

유일한 새 구현이다. 세 경로로 나뉜다.

### 정상 경로 (`builder.py` 패턴 그대로)

```
AskUserQuestion 호출
  → _on_can_use_tool 가로채기
  → normalize_questions_payload()        ← 기존 정규화 재사용
  → S3 put("pending/{pid}.json")         ★신규
  → kind=questions 발행 (SSE)
  → asyncio.Future 대기 (턴 보류)

POST /answers
  → Future.set_result(answers)
  → S3 delete("pending/{pid}.json")      ★신규
  → PermissionResultAllow(answers 주입) → 턴 재개
```

`interrupt_id`는 우리가 `uuid4`로 만든다(Strands가 주던 것의 대체 —
`builder.py`가 이미 그렇게 한다).

### 재시작 경로

Future가 없으면 재시작으로 판단한다.

```
POST /answers, Future 없음
  → resume=session_id 로 클라이언트 연결
  → 답변을 텍스트 턴으로 query()
     "[질문 답변] Q1. PR/FAQ 초안을 승인하시겠습니까? → 승인 — 다음 단계로 진행"
  → 모델이 트랜스크립트의 맥락을 이어받아 진행
  → S3 delete("pending/{pid}.json")
```

도구 호출 결과가 아니라 새 사용자 발화로 들어가는 것이 이 방식의 대가다. 대신
새 상태를 만들지 않고 SDK 내부 계열에 의존하지 않는다.

### 복원

```
GET /pending
  → 인메모리 확인 (정상 경로)
  → 없으면 S3 조회 (새로고침 / 백엔드 재시작)
```

**pending payload:** `{interrupt_id, questions, sdk_questions, session_id}`.
`sdk_questions`(SDK 원형)를 함께 저장하는 이유는 답변을 SDK 라벨로 되번역할 때
필요하기 때문이다 — `builder.py`의 `_answer_to_sdk()`가 그 일을 하고, 재시작
후에는 인메모리 사본이 없다.

### 정규화 통합

`questions_payload.py`(Discovery)와 `builder.py`의 `_to_question_file()`
(프로토타입)이 같은 일을 한다. `_on_can_use_tool`이 양쪽 공통이 되니 하나로
합친다. 오늘 고친 `is_other` 중복 교정이 프로토타입 빌더에도 적용되는 부수
효과가 있다.

### 에러 처리

S3 put이 실패하면 턴을 죽이지 않고 로그만 남기고 진행한다. pending 영속은 복원
편의이고, 그것 때문에 진행 중인 질문을 잃는 게 더 큰 손실이다.
`runner.py`의 `_sync_abandoned_turn()`이 같은 판단을 이미 한다.

---

## 도구: 6개 → 2개

| 현재 (`agent/tools.py`) | 목표 |
|---|---|
| `file_read` | **내장 Read** — `aiplc-rules/` 프리픽스 특수 처리 소멸 |
| `file_write` | **내장 Write** |
| `file_append` | **내장 Edit** |
| `ask_questions` | **내장 AskUserQuestion** — SDK가 스키마 강제 |
| `report_stage` | **커스텀 유지** — 스테이지 사이드바 |
| `submit_document` | **커스텀 유지** — 문서 패널 |

`file_changed` 이벤트는 `PostToolUse` 훅(`Write|Edit|MultiEdit`)이 발행한다
(`builder.py`의 `_on_post_tool_use()`와 동일). `report_stage`가
`aiplc-state.md`를 upsert하는 로직(`state_sync.py`)은 그대로 유지한다.

---

## 테스트

핵심은 **계약 테스트를 두 드라이버가 공유**하는 것이다. `runner.py`가 세
메서드만 쓰므로 그 계약을 `tests/driver_contract.py`로 뽑아 `StrandsDriver`와
`ClaudeDriver`에 같이 걸면 기능 동등을 기계적으로 증명할 수 있다. 삭제된
`sandbox_contract.py`가 같은 패턴이었다.

가짜 SDK는 `tests/fakes/fake_sdk.py`(builder 테스트용)를 재사용한다.

새로 고정할 것:

- **pending 영속 3경로** — 정상 왕복 / 새로고침 복원 / 재시작 후 답변
- **워크스페이스 룰 배치** — `CLAUDE.md` 내용이 `core-workflow.md`와 동일,
  `aws-aiplc-rule-details/` 존재, 이미 있으면 건너뜀
- **config dir 분리** — Discovery 클라이언트가 `proto-config`를 보지 않음
  (스킬 누출 방지)
- **env 토글** — `strands`면 `StrandsDriver`, 기본이면 `ClaudeDriver`
- **정규화 통합** — `is_other` 중복 교정이 양쪽 경로에서 동작
- **활동 라벨 (프론트)** — Claude SDK 도구명(`Write`/`Read`/`Edit`/
  `AskUserQuestion`/`Glob`)과 Strands 도구명 양쪽이 한글 라벨로 매핑되고,
  **영어 도구명이 사용자에게 노출되지 않는다**. `AiMessage.test.tsx`에 이미
  `file_read`/`ask_questions` 케이스가 있으니 새 이름을 나란히 고정한다.

### 유닛 테스트로는 부족한 것

2026-07-26의 미들웨어 Edge 런타임 500이 교훈이다 — 유닛 테스트가 통과했는데
실제 런타임에서 죽었다. 실제 워크숍 턴 한 번(`Workspace Detection` →
`Envision` 진입 + 질문 왕복 1회)을 배포 환경에서 돌리는 수동 체크리스트를 함께
쓴다. `docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md`가
선례다.

---

## 단계 (커밋 단위)

1. **`workspace_rules.py` + `pending_store.py`** — 순수 함수, 드라이버 없이
   테스트 가능
2. **`driver_contract.py`** — 기존 `StrandsDriver`에 **먼저** 걸어 계약을 확정.
   동작하는 기존 코드로 계약을 못박으면 3번에서 그 테스트가 그대로 스펙이 된다
3. **`ClaudeDriver` + `discovery-config/` + 도구 정리**
4. **프론트 활동 라벨 확장** — `ACTIVITY_LABELS`에 Claude SDK 도구명 추가,
   첨부 안내 문구에서 `file_read` 언급 제거. 3번과 독립이므로 **먼저 해도 된다**
   (기존 키를 남기니 Strands 드라이버에 무해하다)
5. **env 토글 배선 + 인프라(user-data env 주입) + 문서**

## 인프라

`proto-config/`가 리포 에셋으로 `/opt/pathfinder/proto-config`에 실리고
user-data가 `PATHFINDER_PROTO_CONFIG_DIR`을 주입하는 것과 같은 경로를 탄다.
에셋은 리포 루트 전체를 올리므로(`pathfinder-hosting-stack.ts:98`) 디렉터리
추가만으로 실리고, user-data에 `PATHFINDER_DISCOVERY_CONFIG_DIR`과
`PATHFINDER_DISCOVERY_DRIVER` env가 추가된다. `.gitignore`가
`proto-config/projects/`·`.credentials.json` 등을 제외하듯 `discovery-config/`도
같은 런타임 산출물 제외가 필요하다.

## 남은 리스크

- **`setting_sources`의 project 스코프가 워크스페이스 `CLAUDE.md`를 실제로
  읽는지** 미확인. 문서상 그렇지만 실제 동작은 1단계 착수 후 확인해야 한다.
  읽지 못하면 통합 규약과 함께 config dir 쪽으로 합치는 폴백이 있다.
- **재시작 후 답변이 텍스트 턴으로 들어가므로** 모델이 그것을 질문 답변으로
  해석하지 못할 수 있다. 프롬프트 문구로 완화하되, 실제 워크숍 검증이 필요하다.
