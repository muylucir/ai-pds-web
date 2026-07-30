# 프로토타입 빌드 세션 — 빌드 1회로 수명 재정의

## 문제

빌드 세션이 너무 오래 살아 있다. 세션 하나가 `claude` 서브프로세스(~300-500MB
RSS, `next build`가 붙으면 최대 2GB)와 전역 빌드 슬롯 하나
(`PATHFINDER_PROTO_MAX_CONCURRENT`, 기본 10)를 붙잡는데, 그 수명이 **사용자가
드로어를 `완료`로 닫을 때까지 또는 30분 유휴**다. 프로토타입이 다 만들어진
뒤에도 마찬가지다.

빌드가 실제로 끝났다는 신호가 시스템에 없다. 에이전트는 매 턴 끝에 `done`을
보내고(`proto/builder.py`의 `ResultMessage` → `AgentEvent(kind="done")`), 그것은
"이 턴이 끝났다"는 뜻일 뿐이다. `_WORKING_STATUSES`가 `"ready"`를 일부러
제외하는 것(`routes/prototypes.py:97`)도 같은 사실의 다른 면이다 — `ready`는
"또 다른 턴을 받을 수 있다"는 뜻이고, 세션은 그래서 열린 채 남는다.

그 결과 현장에서 세 가지가 걸린다.

**1. 빌드가 끝나도 호스팅이 막힌다.** `start_host`는 `_live_session`이 있으면
409 "빌드 세션이 진행 중입니다 — 세션을 먼저 종료해 주세요"를 던진다
(`routes/prototypes.py:511-514`). 그런데 목록은 이미 `built`("빌드 완료 /
호스팅 시작")를 보여준다 — `ready`가 `_WORKING_STATUSES`에 없으므로. 카드가
권하는 버튼이 실패한다. 드로어를 `완료`로 닫으면 해소되지만, 새로고침하거나
페이지를 이탈하면 30분간 슬롯이 잡힌 채 호스팅이 막힌다.

**2. 유휴 타이머가 살아 있는 세션을 죽인다.** `_arm_idle_timer()`는 `start()`,
`send_message()` 진입, `send_answers()` 세 곳에서만 호출된다
(`proto/session.py:169,175,210`) — 턴 **내부**에서는 재무장하지 않는다. 그래서
30분을 넘는 빌드 턴은 진행 중에 죽고, 질문 카드를 띄운 `waiting_input` 상태로
30분이 지나면 세션이 닫히면서 `_on_can_use_tool`의 future가 취소돼
답변 제출이 409가 된다(`proto/builder.py:859-864` → `_drop_dead_question_cards`).

**3. 개선 작업의 비용이 계속 커진다.** 완료 후에도 같은 세션을 이어가면 컨텍스트가
단조 증가한다. 버튼 색 하나 바꾸는 요청이 빌드 전체 트랜스크립트를 지고 간다.

## 이 설계가 하는 일

세션 수명을 **"에이전트가 완료를 선언할 때까지"**로 바꾼다.

```
[빌드 시작] → 세션 A (fresh)
   → 계획 → 승인 → 빌드 …
   → 에이전트가 build_complete 도구 호출
   → build_complete 이벤트(SSE) + handoff.json(S3)
   → done → 세션 A 자동 close()   ← 슬롯·서브프로세스 즉시 회수
        ↓
   드로어에 완료 카드: [호스팅 시작] [개선 이어서 하기] [닫기]
        ↓
   "개선" → POST /session (새 session_id) + handoff.json 주입 → 세션 B
```

## 결정 사항

| 질문 | 결정 | 이유 |
|---|---|---|
| 완료를 어떻게 판정하는가 | **에이전트가 MCP 도구로 명시적 선언** | 도구 호출은 관측 가능한 사실이다. 프롬프트 준수나 산출물 휴리스틱(빌드 중간 턴을 완료로 오판)에 의존하지 않는다 |
| 세션을 누가 닫는가 | **백엔드** (프론트의 `DELETE /session`이 아니라) | 새로고침·탭 닫기에도 슬롯이 회수된다. 프론트가 닫으면 이 문제(#1)가 그대로 남는다 |
| 닫는 시점 | **유휴 타이머를 짧은 유예로 재무장** (지연 값은 호출자가 아니라 타이머가 상태에서 파생) | 제너레이터 안에서 `close()`를 부를 수 없다 — 아래 "종료 시점" 참조 |
| 완료 직후 화면 | **드로어 안 완료 카드 + 다음 행동 버튼** | 드로어를 자동으로 닫으면 빌드 로그를 더 읽으려는 사용자에게 갑작스럽다 |
| 개선 세션의 맥락 | **새 session_id + 요약만 주입** (전액 resume 아님) | 개선 지시에 집중하고 토큰이 가볍다. 요약은 에이전트가 완료 선언 시 직접 쓴다 |
| 완료 카드를 기존 질문 위저드로 그리는가 | **아니다, 전용 카드** | 질문 카드의 답은 에이전트 턴으로 돌아가는 것이 계약(`_on_can_use_tool`의 future)이다. 여기서는 세션을 닫으므로 돌아갈 곳이 없어 특례 분기가 필요해진다 |
| 요약 생성 장치 | **만들지 않는다** | 에이전트가 도구 인자로 직접 쓴다. 별도 요약 단계는 토큰과 실패 지점을 하나 더 만든다 |

## 1. 완료 선언 — `build_complete` MCP 도구

`backend/pathfinder/proto/tools.py`(신규). Discovery의 `agent/tools.py`와 같은
형태 — 명시적 JSON Schema 딕셔너리를 쓴다(`@tool`의 dict 숏컷은 모든 키를
required로 만든다, `agent/tools.py:32-41`의 주석 참조).

```python
_BUILD_COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary":   {"type": "string"},   # 무엇을 만들었는가
        "remaining": {"type": "string"},   # 남은 작업/알려진 한계 (생략 가능)
    },
    "required": ["summary"],
}
```

핸들러가 하는 일은 **빌더의 큐에 이벤트를 넣는 것뿐**이다 —
`_on_post_tool_use`가 `file_changed`를 넣는 것과 동형
(`proto/builder.py:462-472`):

```python
self._queue.append(AgentEvent(kind="build_complete", payload=json.dumps(
    {"summary": summary, "remaining": remaining}, ensure_ascii=False)))
```

**산출물 검증.** `prototype/` 디렉토리가 없거나 비어 있으면 완료를 거부하고,
에이전트가 읽고 재시도할 텍스트를 돌려준다. `_on_can_use_tool`이 잘못된
`AskUserQuestion` payload를 `PermissionResultDeny`로 되돌리는 것과 같은 방향
(`proto/builder.py:505-511`). 판정은 `_local_build_exists`와 같은 기준
(`prototype/`의 직속 자식이 하나라도 있는가, `routes/prototypes.py:155-170`).

### SDK 배선

`_default_client_factory`(`proto/builder.py:153-207`)에 Discovery와 동형의
3줄을 추가한다(`agent/claude_driver.py:423-439`):

```python
server = create_sdk_mcp_server(name=_MCP_SERVER_NAME, tools=build_proto_tools(...))
options = ClaudeAgentOptions(
    ...,
    mcp_servers={_MCP_SERVER_NAME: server},
    allowed_tools=[f"mcp__{_MCP_SERVER_NAME}__build_complete"],
)
```

`_MCP_SERVER_NAME`은 Discovery의 `"pathfinder"`와 구분되는 값을 쓴다(예:
`"pathfinder_proto"`) — 두 드라이버는 서로 다른 도구 집합을 노출한다.
`allowed_tools`의 항목은 반드시 `mcp__<서버 키>__<도구 이름>`으로 적어야
한다(다른 표기는 조용히 승인 대기로 남는다, `claude_driver.py:419-422`).

**`skills="all"`과 충돌하지 않는다 — 확인함.** SDK의 `_apply_skills_defaults`는
`allowed_tools`를 **복사한 뒤** `"Skill"`을 덧붙인다
(`claude_agent_sdk/_internal/transport/subprocess_cli.py:434-452`). shadcn-design
스킬이 그대로 살아 있다.

### 프롬프트

`_plan_prompt()`의 빌드 규칙에 완료 선언 지시를 추가한다. 이 프롬프트가 유일한
브레이크라는 성질은 그대로다(`proto/session.py:252-266`의 docstring).

## 2. 완료 관찰 — 세션이 릴레이하며 상태를 바꾼다

빌더와 세션 사이에 새 배선을 만들지 않는다. `send_message`가 `questions`
이벤트에서 `interrupt_id`를 뽑아 상태를 바꾸는 로직이 이미 있다
(`proto/session.py:179-189`). 여기에 분기를 하나 더 넣는다:

```python
elif event.kind == "build_complete":
    completion = _completion_from(event.payload)   # None이면 무시
    if completion is not None:
        self._completion = completion
        self.status = "complete"
        try:
            await self._write_handoff(completion)
        except Exception:
            _log.exception("handoff write failed: %s/%s", ...)
```

`builder_factory` 시그니처(`Callable[[str, bool], BuilderLike]`)와 `BuilderLike`
Protocol은 바뀌지 않는다. 빌더는 세션을 모른다.

**`_write_handoff`의 예외를 반드시 삼켜야 한다.** 그러지 않으면
`send_message`의 `except Exception`이 잡아 `status = "failed"` + 슬롯 release로
가는데(`proto/session.py:191-200`), 그것은 "handoff 실패에도 완료는 진행한다"는
결정과 반대다. S3 실패가 완료된 빌드를 실패로 보이게 만들면 안 된다.

**payload 파싱은 fail-soft.** `_interrupt_id_from`의 규율을 따른다
(`proto/session.py:45-55`) — 깨진 payload는 예외가 아니라 `None`으로 강등되고,
그러면 완료 처리가 일어나지 않아 유휴 타이머가 평소대로 정리한다.

### `done`이 `complete`를 덮어쓰지 못하게 한다

같은 루프의 기존 분기가 `done`에서 `self.status = "ready"`를 한다
(`proto/session.py:184-185`). `build_complete` 다음에 **반드시** `done`이 오므로,
그대로 두면 상태가 `ready`로 되돌아가 4절의 `_DEAD_STATUSES` 기구가 전부
무력해진다. `done`/`error` 분기를 완료 여부로 가드한다:

```python
elif event.kind in ("done", "error"):
    if self._completion is None:
        self.status = "ready"
```

**순서는 보장돼 있다.** `run()`이 terminal 이벤트를 held하고 큐를 먼저 비운 뒤
마지막에 내보내므로(`proto/builder.py:604-612`, call site 4), `build_complete`는
언제나 `done`보다 앞선다. 이 설계는 그 기존 규율에 의존하며, 그것을 되돌리면
여기가 깨진다.

### `AgentEvent` 확장

- 백엔드 `models.py:60-61` — `kind` Literal에 `"build_complete"` 추가
- 프론트 `lib/api/types.ts:57-65` — `AgentEventKind` 유니온에 추가
- 프론트에 `BuildCompletePayload {summary, remaining?}` 타입 추가
  (`QuestionsPayload`와 같은 자리)

`kind`가 프론트 계약이므로 양쪽을 함께 바꾼다.

## 3. 종료 시점 — 타이머로 예약한다

**`send_message`의 `async for` 뒤에 `close()`를 두면 안 된다.**

`sse.ts:29`가 `done`에서 EventSource를 닫는다. 그러면 sse_starlette가 그 프레임을
write하는 await에서 태스크가 죽고, 제너레이터 체인
(`_relay_queue` → `run` → `send_message` → `gen`)은 마지막 `yield`에 영원히
매달린다 — `proto/builder.py:355-370`이 실측으로 문서화한 그 창이다. 루프 뒤
코드는 실행되지 않는다.

그래서 종료는 **유휴 타이머 기구를 재사용해 예약**한다. `_arm_idle_timer`는 이미
`loop.call_later` → `create_task(close())`로 제너레이터와 분리돼 있다
(`proto/session.py:142-149`).

**지연 값은 `_arm_idle_timer` 자신이 결정한다 — 호출자가 인자로 넘기지 않는다.**
이것이 5절의 "매 이벤트마다 재무장"과 충돌하지 않는 유일한 형태다:

```python
def _arm_idle_timer(self) -> None:
    delay = (_COMPLETION_GRACE_SECONDS if self._completion is not None
             else self._idle_seconds)
    ...  # 기존 cancel + call_later 그대로
```

호출자가 값을 넘기는 형태였다면 `build_complete`가 짧은 유예로 무장한 직후
뒤따르는 `done`이 기본 30분으로 되돌려 세션이 닫히지 않는다 —
`build_complete` 다음에는 **반드시** `done`이 오므로 이것은 가능성이 아니라
확정된 동작이다. 지연을 상태에서 파생시키면 그 창이 존재하지 않는다.

이 선택이 주는 것:

- 클라이언트가 무엇을 하든(새로고침, 탭 닫기, 정상 수신) 슬롯이 회수된다.
- 유예가 terminal 이벤트에 체인을 빠져나갈 여유를 준다.
- **새 종료 경로가 생기지 않는다.** `close()`의 멱등성 가드
  (`_closed`/`_slot_released`, `proto/session.py:105-109`)가 이미 이중 release를
  막으므로, 타이머와 사용자의 `DELETE /session`이 겹쳐도 슬롯은 정확히 한 번
  풀린다.
- `close()`가 `status`를 `"closed"`로 덮으므로, 그 뒤 `POST /session`은 4절의
  `complete` 분기가 아니라 기존 `closed` 분기를 탄다 — 둘 다 `_DEAD_STATUSES`라
  결과가 같다.

## 4. `complete` 상태 — 결함 #1을 구조적으로 해소

`start_host`를 건드리지 않는다. `SessionStatus` Literal에 `"complete"`를 추가하고
`_DEAD_STATUSES`에 넣으면(`routes/prototypes.py:122`) 네 곳이 동시에 옳아진다:

| 라우트 | 지금 | 변경 후 |
|---|---|---|
| `POST /host` | 409 "세션을 먼저 종료해 주세요" | 통과 — 에이전트가 손 뗐으니 트리 경합이 없다 |
| `POST /session` | 409 "already active" | 통과 — "개선 이어서 하기"가 필요로 하는 것 |
| `POST /answers`·`/interrupt` | 통과(하지만 답할 곳이 없다) | 404 — 정확하다 |
| 목록의 `state` | — | `built` (`complete`는 `_WORKING_STATUSES`에 없다) ✓ |

`_DEAD_STATUSES`는 "새 시작을 막지 않고, 스트림으로 제공되지 않는다"는
질문에 답하는 집합이고 `complete`가 정확히 그 성질이다. 이 두 집합이 하나로
합쳐지면 안 되는 이유는 `routes/prototypes.py:84-97`이 이미 문서화했다.

**유예 종료와의 경합도 이걸로 사라진다**: 상태는 완료 관찰 **즉시** 바뀌므로,
사용자가 유예 창 안에 [호스팅 시작]을 눌러도 409가 아니다. 그 시점에
에이전트는 이미 도구를 호출하고 턴을 마쳤으므로 빌드 트리에 쓰지 않는다.

## 5. 유휴 타이머 재정의 — 결함 #2

타이머의 의미를 **"턴 진입 이후"**에서 **"마지막 생존 신호 이후"**로 바꾼다.
생존 신호 = 릴레이되는 모든 이벤트, 또는 사용자 행동. 구현은 `send_message`의
릴레이 루프 안에서 `_arm_idle_timer()`를 부르는 것뿐이다 — 3절에서 지연 값을
상태에서 파생시켰으므로 이 호출은 완료 유예를 되돌리지 않는다.

두 결함을 동시에 덮는다:

- **긴 빌드** — `status`/`file_changed`가 계속 흐르므로 30분을 넘겨도 죽지 않는다.
- **질문 대기** — `questions` 이벤트 자체가 재무장 지점이라, 카드가 뜬 순간부터
  유휴 예산이 새로 시작한다. 사용자에게 온전한 30분이 주어지고, 방치하면 슬롯은
  회수된다(그 회수는 의도된 동작이다).

**비용을 명시해 둔다**: 재무장은 `TimerHandle.cancel()` + `call_later` 한 쌍이고,
빌드 한 번에 수천 번 일어난다. 둘 다 힙 연산 하나짜리이므로 실질 비용은 없지만,
이벤트마다 부르는 형태라는 점은 알고 있어야 한다.

## 6. 개선 세션 — `_resolve_session_id`의 세 번째 분기

`handoff.json`을 `prototypes/{slug}/handoff.json`에 쓴다:

```json
{"summary": "...", "remaining": "...", "completed_at": "2026-07-30T..."}
```

`prototypes/{slug}/` 프리픽스 안이므로 `purge_session_state`가 이미 지운다
(`proto/session.py:335`) — 초기화 처리를 새로 넣을 필요가 없다.

`_resolve_session_id`는 지금 2분기다 — 저장된 UUID가 있으면 resume, 없으면
fresh(`proto/session.py:124-138`). 3분기로 늘린다:

| 조건 | session_id | 프롬프트 |
|---|---|---|
| `session.json` 없음 | 새 UUID | `_plan_prompt()` (기존) |
| 있음, handoff 없음 | 저장된 것 **resume** | `_resume_prompt()` (기존) |
| 있음 + **handoff 있음** | **새 UUID** (저장하고 handoff 삭제) | `_handoff_prompt()` (신규) |

세 번째 분기는 새 `session_id`를 `session.json`에 저장하고 `handoff.json`을
지운다. 그러면 그 세션이 완료 선언 없이 죽어도(유휴, 재시작) 다음 시작이 두 번째
분기로 떨어져 개선 대화를 resume한다 — 정확한 동작이다.

**순서: `session.json` 쓰기 → handoff 삭제.** 그 사이에서 실패하면 handoff가
남아 다음 시작이 다시 세 번째 분기를 타는데, 그때 `session.json`에는 이미 새
(빈) session_id가 있으므로 개선 프롬프트로 새로 시작한다 — 같은 결과다.
반대 순서는 handoff를 지운 뒤 session_id 쓰기가 실패하면 요약을 잃고 옛
세션을 전액 resume한다. 손실 있는 방향을 피한다.

`_resolve_session_id`의 반환 형태가 `tuple[str, bool]`에서 3분기를 표현할 수
있는 형태로 넓어진다(예: 프롬프트 종류를 나르는 `Literal`). `start()`가 그
값을 `self._resumed` 대신 `self._prompt_kind`에 저장하고 `first_prompt()`가
그것으로 분기한다.

**`_resume_prompt()`가 남는 이유**: 완료 선언 **없이** 세션이 죽은 경우는 여전히
진짜 resume이 맞다. 두 경로는 다른 사건을 표현한다.

`_handoff_prompt()`는 `_resume_prompt()`보다도 짧다 — 스펙 경로, handoff의 요약과
남은 작업, 그리고 "무엇을 개선할지 `AskUserQuestion`으로 물어라". 파일 트리는
넘기지 않는다: 에이전트가 자기 파일 도구로 cwd를 읽는 편이 스냅샷보다 정확하고,
그게 이미 스펙을 읽는 방식이다(`proto/session.py:160-165`).

## 7. 프론트엔드

### `usePrototypeStream.ts`

`applyEvent`에 `build_complete` 분기를 추가한다(명시적 분기가 없으면 조용히
무시된다). `pendingQuestions`와 같은 형태의 `buildComplete` 상태를 두고
`BuildCompletePayload`를 담는다. 파싱은 `safeParse`로 fail-closed
(`usePrototypeStream.ts:26-33`).

이 이벤트는 `streaming`을 건드리지 않는다 — 뒤따르는 `done`이 `onDone`으로
평소대로 턴을 닫는다.

"개선 이어서 하기"는 `startSession` → `startBuild()`다. `startBuild`가 여는
`__first__` 센티넬이 서버에서 `first_prompt()`로 치환되고, 그것이 6절의 세 번째
분기를 타 `_handoff_prompt()`가 된다. **새 API가 필요 없다.**

개선 세션을 시작하면 `buildComplete`를 비운다.

### `BuildPanel.tsx`

`buildComplete`가 있으면 우측 패널(`aside`)에 완료 카드를 렌더한다 — 질문 폼이
있던 자리와 같은 곳. 요약과 남은 작업을 보여주고 버튼 세 개:

| 버튼 | 동작 |
|---|---|
| 호스팅 시작 | `startHost` → 성공 시 `onClose()`로 그리드 복귀(카드가 `running`) |
| 개선 이어서 하기 | 위의 startSession → startBuild |
| 닫기 | 기존 `handleDone` (`closeSession`은 이미 닫힌 세션에 404 → 무해하게 흡수) |

`handleDone`이 404를 만나면 그대로 `onClose()`로 진행한다 — 백엔드가 먼저 닫은
것이 정상 경로다. 같은 이유로 `closeSession`의 404는 세 버튼 모두에서 성공으로
취급한다.

**유예 창 안에 [호스팅 시작]을 눌러도 안전하다.** 그 시점에 세션은 아직
`proto_sessions`에 있지만 상태가 `complete`이므로 `start_host`의 `_live_session`
검사를 통과하고(4절), `ProtoHost.start`는 빌드 트리를 지우지 않는다
(`proto/host.py:158-162` — "NOT rmtree: the builder writes into this very
directory"). 유예가 만료되면 `close()`가 서브프로세스만 정리한다. 호스팅
프로세스는 `ProtoHost`가 별도로 소유하므로 영향이 없다.

## 에러 처리

| 상황 | 처리 |
|---|---|
| 산출물 없이 완료 선언 | 도구가 거부하고 에이전트가 읽을 텍스트를 돌려준다(위 1절) |
| `handoff.json` 쓰기 실패 | 완료 자체는 진행(세션 닫히고 카드 뜸). 예외를 삼켜야 한다 — 2절 참조. 다음 시작은 handoff가 없어 2번 분기 → 전체 resume. 무겁지만 정확한 degradation |
| 개선 세션 시작 중 `session.json` 쓰기 실패 | handoff가 남아 다음 시작이 다시 3번 분기 → 같은 결과(6절의 순서 근거) |
| 에이전트가 도구를 끝내 안 부름 | 유휴 타이머가 정리, 다음 시작이 resume. **현재 동작 그대로** — 이 설계는 회귀를 만들지 않는다 |
| 완료 이벤트 payload 손상 | `None`으로 강등, 완료 처리 없음, 유휴 타이머가 정리 |
| 개선 시작이 429 | 기존 상한 메시지 그대로 |
| 완료 후 `POST /answers` | 404 (`complete`는 `_DEAD_STATUSES`) — 답할 future가 없으니 정확하다 |
| 초기화 | `purge_session_state`가 handoff까지 지워 fresh로 복귀. 추가 처리 불필요 |

## 테스트

**백엔드** (`test_proto_builder.py`, `test_proto_session.py`, `test_routes_prototypes.py`)

- `build_complete` 도구가 큐에 이벤트를 넣는다
- `prototype/`이 비어 있으면 완료를 거부한다
- 완료 관찰 → `status == "complete"`, `handoff.json` 기록
- **뒤따르는 `done`이 `status`를 `ready`로 되돌리지 않는다** (2절 가드)
- **`build_complete` → `done` 순서로 릴레이된다** (`run()`의 terminal held 규율에
  의존하므로, 그 순서가 깨지면 여기가 먼저 실패해야 한다)
- **`_write_handoff` 실패가 `status`를 `failed`로 만들지 않고 슬롯을 풀지 않는다**
- 유예 후 세션이 닫히고 슬롯 release가 **정확히 1회** (`_slot_released` 가드)
- 사용자 `DELETE /session`과 유예 종료가 겹쳐도 release 1회
- **완료 후 `done`이 타이머를 기본값으로 되돌리지 않는다** (3절: 지연이 상태에서
  파생된다 — 짧은 `_COMPLETION_GRACE_SECONDS`로 실측)
- `complete` 상태에서 `_live_session`이 None → `POST /host`가 409를 내지 않는다
- `complete` 상태에서 `POST /answers`가 404
- 목록에서 `complete` 세션이 `built`로 보인다
- `_resolve_session_id` 3분기 각각 / `first_prompt` 3형태
- 세 번째 분기가 새 id를 저장하고 handoff를 지운다
- 이벤트마다 타이머 재무장 (짧은 `idle_seconds` 실측 — 기존 테스트 방식,
  `test_proto_session.py:278`)
- **질문 대기 중에도 재무장된다** — `questions` 릴레이 후 유휴 예산이 리셋된다
- 깨진 `build_complete` payload가 예외를 던지지 않는다 (`status`는 `building` 유지)

**프론트엔드** (`usePrototypeStream.test.tsx`, `BuildPanel.test.tsx`)

- `build_complete` → `buildComplete` 상태 설정, `streaming` 불변
- 깨진 payload → `null`, 스트림 계속
- 완료 카드가 요약·남은 작업·버튼 3개를 렌더
- [호스팅 시작] → `startHost` 호출
- [개선] → `startSession` + 스트림 재개, `buildComplete` 비움
- [닫기]가 404를 흡수하고 `onClose()` 진행

## 변경 파일

| 파일 | 성격 |
|---|---|
| `backend/pathfinder/proto/tools.py` | 신규 — `build_complete` + 산출물 검증 |
| `backend/pathfinder/proto/builder.py` | `_default_client_factory`에 MCP 서버·`allowed_tools` 배선. `PrototypeBuilder.__init__`이 산출물 검증용 경로를 알아야 하므로 인자 하나 추가(`workspace` 하위 `prototype/`이라 파생 가능하면 불필요) |
| `backend/pathfinder/proto/session.py` | 완료 관찰, `done` 가드, `complete` 상태, 타이머 지연 파생 + 이벤트별 재무장, handoff 읽기/쓰기, `_resolve_session_id` 3분기, `_prompt_kind`, `_handoff_prompt` |
| `backend/pathfinder/models.py` | `kind` Literal에 `build_complete` |
| `backend/pathfinder/routes/prototypes.py` | `_DEAD_STATUSES`에 `complete` |
| `frontend/lib/api/types.ts` | `AgentEventKind` + `BuildCompletePayload` |
| `frontend/lib/usePrototypeStream.ts` | `buildComplete` 상태, `applyEvent` 분기, 개선 재시작 |
| `frontend/components/prototypes/BuildPanel.tsx` | 완료 카드 |
| `README.md` | 세션 수명 서술 갱신 — 현재 "세션을 닫거나 백엔드가 재시작돼도 맥락이 사라지지 않고"(README.md:13)는 유지되지만, 세션이 **완료 선언 시 스스로 닫힌다**는 사실과 개선 분기를 추가한다 |

## 범위에서 뺀 것

- **완료 후 자동 호스팅** — 사용자가 [호스팅 시작]을 누르는 편이 낫다. 호스팅
  실패(`npm install`/`next build`) 에러 처리가 완료 경로에 섞이면 두 실패가
  구분되지 않는다.
- **개선 세션의 파일 트리 스냅샷** — 에이전트가 cwd를 직접 읽는다.
- **완료 이력** — `handoff.json`은 최신 하나만 유지한다. 워크숍 한 세션에서 여러
  번 완료-개선을 돌 수 있지만, 이력을 보여줄 화면이 없다.
- **`_MessageReader` 구조** — `_translate`의 미배달 메시지 손실
  (`proto/builder.py:320-333`)은 이 설계와 독립적인 선재 결함이다.
