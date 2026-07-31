# 히스토리 로딩 표시 · 턴 중단 버튼

## 문제

두 가지가 별개지만 같은 화면(채팅)에서 같은 원인 — **진행 중임을 알려주지 않는다** — 을
공유한다.

**1. 히스토리 로딩이 보이지 않는다.** 워크스페이스에서 나갔다 들어오면
`GET /projects/{pid}/history`가 도는 동안 채팅 영역이 **빈 화면**이다.
`historyLoading` 상태는 이미 있지만 웰컴 카드를 가리는 데만 쓰이고
(`workspace/page.tsx:37`) 화면에는 아무 표시도 없다. 대화가 많을수록 이 구간이
길어져 사용자는 "내 대화가 사라졌다"고 읽는다.

**2. 진행 중인 턴을 멈출 수 없다.** 프로토타입 빌드 패널에는 중단 버튼이 있지만
(`BuildPanel.tsx:123`) 워크스페이스에는 없다. 에이전트가 엉뚱한 방향으로 길게
가고 있어도 끝날 때까지 기다려야 한다 — 워크숍에서 시간이 가장 아까운 구간이다.

Discovery 쪽은 **드라이버·라우트·UI 전부 없다.** `interrupt()`는
`proto/builder.py:750`과 `proto/session.py:440`에만 있고 `ClaudeDriver`에는 없다.

## 결정 사항

| 질문 | 결정 | 이유 |
|---|---|---|
| 중단하면 지금까지 한 작업은 | **살린다** | 만들어진 파일과 나온 텍스트를 그대로 두고 다음 입력을 받는다. 롤백은 구현이 무겁고 살려도 될 작업을 버린다. 프로토타입 중단과 같은 동작 |
| 로딩 표시 모양 | **스켈레톤 말풍선 3개** | "대화가 여기 들어선다"를 예감하게 한다. 로드 직후 레이아웃 이동이 가장 적다 |
| 중단 버튼 위치 | **전송 버튼 자리에서 ■로 전환** | `ChatInput` 하나를 고치면 두 화면에 함께 적용된다. 스트리밍 중 이미 버효화되던 자리라 레이아웃 변화가 없다 |
| 프로토타입 헤더의 기존 중단 버튼 | **제거** | 두 화면이 같은 자리·같은 모양으로 동작한다. 같은 기능 버튼이 한 화면에 둘이면 헷갈린다 |
| 중단 후 표시 | **말풍선 아래 "중단됨" 한 줄** | 스크롤백을 나중에 봐도 여기서 끊긴 것을 알 수 있다. 트랜스크립트에 남으므로 복원 시에도 재현된다 |

## 기능 1 — 히스토리 로딩 표시

### 범위

워크스페이스만. 프로토타입 챗은 히스토리 복원 경로가 없다(빌드 드로어는 세션
트랜스크립트를 SDK가 resume으로 이어받고, 별도 `GET /history`가 없다).

### 컴포넌트

`components/canvas/HistorySkeleton.tsx` (신규):

- AI 아바타(기존 `AiMessage`와 같은 모양) + 회색 펄스 박스 3개
- 폭을 다르게(`w-3/5`, `w-4/5`, `w-2/5`) 두어 실제 대화처럼 보이게 한다
- `animate-pulse`
- `role="status"` + `aria-label="이전 대화를 불러오는 중"` — 화면에는 형태로,
  스크린리더에는 문구로 전달한다

### 배선

`workspace/page.tsx`에서 `historyLoading`이 참일 때 채팅 목록 자리에 렌더한다.

기존 `showWelcome` 조건(`!historyLoading && items.length === 0 && ...`)은 그대로
둔다 — 웰컴 카드와 스켈레톤이 동시에 뜨지 않는 것이 이미 그 조건으로 보장된다.

**라이브 턴이 로딩 중에 시작되는 경우:** `historyLoading`이 거짓이 되면 스켈레톤이
사라지고 실제 항목이 들어온다. 히스토리 prepend 로직(`useWorkspaceStream`의
`liveStartedRef`)은 손대지 않는다 — 순서 보장은 이미 그쪽이 한다.

## 기능 2 — 턴 중단

### UI (공유 컴포넌트 1곳)

`components/canvas/ChatInput.tsx`에 두 prop 추가:
`onInterrupt?: () => void`, `interrupting?: boolean`.

| `interrupting` | 버튼 | 활성 |
|---|---|---|
거짓/미지정 | ↑ (전송) | 입력이 있고 `disabled`가 거짓일 때 |
참 | **■ (중단)** | `onInterrupt`가 있을 때 |

**`disabled`를 재사용하지 않고 별도 prop을 두는 이유:** 두 화면에서 `disabled`의
의미가 다르다. 워크스페이스는 `disabled={streaming}`이지만 프로토타입은
`disabled={streaming || buildComplete !== null}`이다(`BuildPanel.tsx:168`) —
`disabled`로 판단하면 **빌드가 끝난 뒤에도 ■이 뜬다.** 중단할 턴이 없는데
중단 버튼이 있는 상태이므로, "입력을 막는다"와 "중단할 턴이 있다"를 분리한다.

호출부는 각자 `interrupting={streaming}`을 넘긴다.

`aria-label`은 상태에 따라 "전송" / "중단"으로 바뀐다.

`BuildPanel.tsx`의 헤더 중단 버튼을 제거하고 `ChatInput`에 `onInterrupt`를 넘긴다.

### 백엔드 — Discovery (신규)

프로토타입 쪽은 이미 완성돼 있어 UI만 바꾼다. Discovery는 세 겹이 필요하다.

**1) `ClaudeDriver.interrupt()`**

`proto/builder.py:750`의 구현을 패턴으로 따르되 한 가지가 다르다 — Discovery의
pending은 **S3에도 미러링된다**(`agent/pending_store.py`). 순서:

1. 진행 중인 턴이 없으면 no-op (멱등)
2. 파킹된 `can_use_tool` future 취소
3. 인메모리 pending 상태 정리 (`_pending_*`)
4. **S3 pending 레코드 삭제** (`clear_pending`)
5. 큐에 남은 `questions` 이벤트 폐기
6. `client.interrupt()`

4번이 이 설계의 핵심이다. 빼먹으면 답할 수 없는 질문이 `GET /pending`으로
복원되어 사용자가 폼을 채우고 제출했는데 아무 일도 일어나지 않는다 —
빌더가 3번을 하는 것과 같은 이유이고, Discovery는 durable 사본이 하나 더 있다.

**2) `AgentRunner.interrupt()`**

드라이버로 위임한다. 턴 슬롯은 진행 중인 `run()`이 스스로 놓으므로 여기서
건드리지 않는다.

**3) `POST /projects/{pid}/interrupt` → 202**

`routes/turns.py`. 워크스페이스가 없으면 no-op으로 202 — 프로토타입
라우트(`/prototypes/{slug}/interrupt`)와 같은 계약이다. 진행 중인 턴이
없을 때도 202: 중단은 멱등이고, 이미 끝난 턴에 대한 중단 요청은 에러가 아니다.

### "중단됨" 표시

드라이버가 중단 시 `AgentEvent(kind="status", text="중단됨")`을 큐에 넣는다.

프론트는 이 status를 **trace의 한 줄이 아니라 말풍선 아래 별도 한 줄**로 렌더한다
— trace는 도구 실행 기록이고 중단은 턴의 종결 사유라 성격이 다르다.
`AiItem`에 `interrupted: boolean`을 두고 `AiMessage`가 그 플래그로 렌더한다.

트랜스크립트에도 남으므로 복원 시 재현된다.

### 중단 후 상태

- 나온 텍스트·만들어진 파일: 그대로
- `streaming`: 거짓 → 입력창이 열리고 바로 다음 메시지를 보낼 수 있다
- 워크스페이스 S3 동기화: `run()`이 종결 이벤트에서 하던 것을 그대로 한다
  (중단도 턴의 끝이다)

## 테스트

| 대상 | 검증 |
|---|---|
`HistorySkeleton` | `historyLoading` 참일 때 렌더, 거짓일 때 사라짐, 웰컴 카드와 동시에 뜨지 않음 |
`ChatInput` | `interrupting` 참일 때 ■ 노출 · 클릭 시 `onInterrupt` 호출 · prop 없으면 기존 동작 · `aria-label` 전환 · **`disabled`만 참이고 `interrupting`은 거짓일 때 ■이 뜨지 않는가**(빌드 완료 상태) |
`ClaudeDriver.interrupt()` | **파킹된 질문이 있을 때 S3 pending이 삭제되는가** (핵심 회귀) · 턴 없을 때 no-op · 중단 후 status 이벤트 |
`POST /interrupt` | 세션 없을 때 202 · 있을 때 드라이버 호출 · 멱등 |
`BuildPanel` | 헤더 버튼이 사라지고 `ChatInput`으로 중단이 되는가 |
실 CLI 프로브 | 중단이 실제로 턴을 끊고 그 시점까지의 트랜스크립트가 S3에 남는가 |

## 하지 않는 것

- **턴 롤백** — 만들어진 파일을 되돌리지 않는다(위 결정 사항)
- **프로토타입 백엔드 변경** — 이미 동작한다. UI만 통일한다
- **프로토타입 챗의 히스토리 스켈레톤** — 그 경로가 없다
- **중단 확인 모달** — 되돌릴 수 없는 동작이 아니다(작업이 살아 있다). 실수로
  눌러도 다음 메시지를 보내면 이어진다
