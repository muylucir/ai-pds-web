# 프로토타입 완전 초기화

## 문제

프로토타입 하나가 남기는 상태가 7곳에 흩어져 있고, 그 전부를 지우는 방법이 없다.
현재 있는 정리 수단은 부분적이다 — `DELETE .../session`은 빌드 세션만,
`DELETE .../host`는 실행 중인 프로세스만 정리한다. 빌드 산출물·트랜스크립트·설문은
남는다.

실무적으로 이것이 막는 것: 잘못 빌드된 프로토타입을 깨끗한 상태에서 다시
시작할 수 없다. 재빌드는 기존 트리 위에 덮어쓰므로 이전 시도의 잔재(예:
`basePath` 없이 굳은 `.next/`)가 남는다.

## 결정 사항

| 질문 | 결정 | 이유 |
|---|---|---|
| 스펙 문서를 지우는가 | **남긴다** | 카드가 `none`으로 남아 같은 스펙으로 재빌드할 수 있다. Discovery 산출물을 프로토타입 탭이 지우지 않는다는 경계(`ca86f1c`)도 유지된다 |
| 설문 응답을 지우는가 | **지운다** | "완전 초기화"의 문자적 의미. 되돌릴 수 없으므로 확인 절차로 보완한다 |
| 라이브 세션·호스팅 | **자동 정리 후 진행** | 사용자가 여러 단계를 거치지 않게 한다 |
| 확인 절차 | **항상 토스트 확인** | 워크숍 현장에서 오통 방지에 충분. 설문 응답이 있으면 갯수를 명시한다 |
| API 형태 | `DELETE .../prototypes/{slug}` | 기존 `DELETE .../session`·`/host`와 계층관계가 맞다 |

## 삭제 대상

프로토타입 하나가 남기는 상태 전부와, 각 항목의 처리:

| # | 위치 | 소유자 | 처리 |
|---|---|---|---|
| 1 | `prototypes/{slug}/survey/` (questionnaire·responses·rollup·archive) | `survey/store.py` | **삭제** |
| 2 | `surveys/by-token/{token}.json` — **프로토타입당 N개** | `survey/store.py` | **삭제** (아래 참조) |
| 3 | `aiplc-docs/discovery/prototypes/{slug}/validation-questionnaire.md` | `survey/store.py` | **삭제** |
| 4 | `prototypes/{slug}/session.json` | `proto/session.py` | **삭제** |
| 5 | `prototypes/{slug}/transcript/` | `proto/session_store.py` | **삭제** |
| 6 | `prototypes/{slug}/bundle/` (레거시) | — | **삭제** |
| 7 | `{proto_root}/{pid}/{slug}/` (로컬 빌드 트리) | `proto/host.py` | **삭제** |
| — | `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md` | Discovery | **보존** (위 결정) |
| — | `aiplc-docs/discovery/prototype/validation-results.md` | `survey/store.py` | **보존** — slug가 없어 프로토타입 간 공유된다. 하나를 초기화하며 지우면 다른 프로토타입의 검증 결과가 날아간다 |

**2번이 특히 중요하고, 단순 삭제가 아니다.** 빠뜨리면 고아 토큰이 남아, 공개 설문
링크는 살아 있는데 가리키는 설문이 없어 응답자가 깨진 페이지를 본다.

토큰 인덱스는 **프로토타입당 하나가 아니다.** `archive_current()`
(`survey/store.py:282-302`)는 설문을 보관할 때 토큰 인덱스를 지우지 않으므로,
설문을 재생성할 때마다 옛 토큰의 인덱스가 남는다 — N번 재생성한 프로토타입에는
N개의 인덱스가 있다.

그리고 `surveys/by-token/`은 **루트 스코프**(`SurveyStore._root`)라 slug로 스캔할
수 없다. 토큰 → 프로토타입 단방향 인덱스이고 역방향이 없다.

따라서 토큰은 questionnaire에서 역으로 수집해야 한다:

```
1. questionnaire.json 에서 현재 토큰 수집
2. archive/{closed_at}/questionnaire.json 각각에서 옛 토큰 수집
3. surveys/by-token/{각 토큰}.json 삭제
4. 그 다음에 prototypes/{slug}/survey/ 트리 삭제
```

**3→4 순서가 load-bearing이다.** 트리를 먼저 지우면 토큰을 알아낼 방법이 사라져
고아 인덱스가 영구히 남는다. 이 제약은 아래 "S3 먼저, 로컬 나중"보다 더 세밀하며,
`SurveyStore.purge()` 내부에서 지켜져야 한다.

## 아키텍처 — 소유자별 위임

```
DELETE /projects/{pid}/prototypes/{slug}          routes/prototypes.py
   │
   ├─ 1. 라이브 정리 (있으면 정리, 없으면 통과)
   │     ├─ proto_sessions[(pid,slug)].close()   빌더 subprocess 종료 + 세마포어 반납
   │     └─ proto_host().stop(pid, slug)         npm 프로세스 종료 + 포트 반납
   │
   ├─ 2. 각 소유자에게 삭제 위임 (S3 먼저, 로컬 나중)
   │     ├─ SurveyStore.purge()                  #1 #2 #3
   │     ├─ purge_session_state()                #4 #5 #6
   │     └─ ProtoHost.purge()                    #7
   │
   └─ 3. 스펙은 건드리지 않음  →  카드가 "none"
```

라우트가 S3 키 규약을 알지 않는다. 각 `purge()`는 자기가 만든 키만 안다.

이번 세션에서 고친 버그 둘(`prototype/` 경로 드리프트, `_LIVE_STATUSES`)이 모두
"같은 규약을 두 곳이 각자 알아서" 생긴 것이었다. 삭제는 그 위험이 특히 크다 —
새 S3 키가 추가되면 삭제 쪽이 조용히 빠뜨리고, 그것은 테스트에 잡히지 않는다.

**`purge_session_state()`는 모듈 함수여야 한다** (인스턴스 메서드가 아니라).
세션이 회수된 프로토타입(빌드 완료 후 정상 상태)도 초기화 대상이므로, 인스턴스에
매달면 호출할 수 없다.

## 실패 처리 — 재시도 가능하게

S3에 트랜잭션이 없어 원자적 삭제는 불가능하다. 부분 삭제를 정상 상태로 받아들이고
재시도 가능하게 만든다:

- 각 `purge()`는 없는 키를 지우는 것을 **성공으로 취급**한다 (idempotent)
- 한 곳이 실패해도 나머지를 계속 시도하고, 마지막에 실패 목록을 모아 **502**
- 사용자는 다시 누르면 된다 — 이미 지워진 것은 건너뛰고 남은 것만 지운다

**순서는 S3 먼저, 로컬 나중이다.** 로컬 디렉터리가 먼저 사라지면
`_local_build_exists`가 `False`가 되어 카드가 `none`이 되고, 사용자는 초기화가
끝난 줄 알지만 S3에 설문·트랜스크립트가 남는다. S3를 먼저 지우면 카드가 여전히
`built`이므로 **미완료가 화면에 드러난다**.

## 라이브 정리

`close_session`(`routes/prototypes.py:248`)은 세션이 없으면 404다. 초기화는 그러면
안 된다 — 세션이 없는 것이 정상이다. "있으면 정리, 없으면 통과"로 다르게 동작한다.

`session.close()`가 세마포어를 반납하므로(`proto/session.py:241-`) 초기화는 빌드
슬롯을 해제한다. 의도된 부수효과다.

## UI

`PrototypeCard`에 "초기화" 버튼(`state !== "none"`일 때만 — 지울 것이 없는 카드에
파괴적 동작을 내놓는 것은 소음이다). 형제 버튼과 같이 `disabled={busy}`를 준다.

**확인은 `role="dialog"` 모달로 한다 — `window.confirm`이 아니다.** 이 앱에는 관례가
이미 두 곳에 있다(`ProjectList.tsx`, `admin/UserTable.tsx`): 오버레이 + `aria-modal`,
되돌릴 수 없음을 독립 문장으로, Escape 취소, 다이얼로그 안의 에러 줄, 그리고 파괴적
행위를 명명하는 확인 버튼("초기화"). `window.confirm`은 긍정 버튼이 OS 기본 "OK"이고
그것이 기본 포커스라 Enter 오타가 실제 응답을 파괴한다.

다이얼로그는 삭제될 항목을 **동사와 함께** 나열하고("다음 항목이 삭제됩니다:"),
설문 응답이 1건 이상이면 갯수와 되돌릴 수 없음을 명시한다. 안심 문구(스펙은 남아
재빌드 가능)를 경고보다 뒤(최신성 위치)에 두지 않는다.

응답 갯수는 목록 응답의 `response_count`로 싣는다(버튼 시점에 이미 알 수 있다).
**다만 다이얼로그를 띄우기 전에 다시 읽는다.** 이 페이지에는 폴링이 없어 0→N 전이가
위험하다 — 페이지 로드 후 응답이 도착하면 갯수도 경고도 없이 "검증 설문"만 보여주어
"잃을 것이 없다"고 단정한 뒤 실제 제출물을 파괴한다. 워크숍에서는 이것이 정상
시퀀스다(진행자가 링크를 공유하고 응답이 세션 중 도착).

`list.reload()`는 `finally`에서 — 부분 실패(502)에서도 지워진 것은 반영해야 하고,
카드가 여전히 `built`면 그것이 미완료 신호다.

## 테스트

- 7곳 상태를 모두 심은 뒤 초기화 → 스펙만 남고 카드가 `none`
- 토큰 인덱스(`surveys/by-token/`)가 지워짐 — 고아 토큰 방지
- **아카이브된 옛 토큰까지** 지워짐 — 설문을 2회 생성해 archive를 만든 뒤,
  두 토큰 모두 인덱스에서 사라지는지 확인. 현재 토큰만 지우는 구현은 이 테스트에서
  실패한다
- 응답이 없어 rollup.json이 없는 설문도 초기화 성공 (`archive_current`의
  `FileNotFoundError` 경로와 같은 부류)
- `validation-results.md`(공유 문서)는 **남아 있음**
- 라이브 세션·호스팅이 있는 상태 → `close`/`stop`이 호출되고 세마포어가 반납됨
- 세션이 없는 프로토타입 → 404가 아니라 성공
- 두 번 연속 초기화 → 두 번째도 성공 (idempotent)
- S3 삭제 실패 → 502, 로컬은 남아 카드가 `built` (미완료가 드러남)

## 범위 밖

- 프로젝트 전체 초기화 (프로토타입 단위만)
- 되돌리기 / 휴지통
- 스펙 문서 삭제 (Discovery에서 한다)
