# 한국어/영어 이중 언어 지원

날짜: 2026-08-03

## 문제

Pathfinder는 한국어 전용으로 만들어졌다. UI 문자열은 코드에 리터럴로 박혀
있고(비테스트 소스 65개 파일, 코드 내 한국어 420줄), 에이전트에게 주는 언어
지시는 네 곳에 흩어져 있다. 영어권 참가자가 있는 워크숍에서는 화면도 산출물도
읽을 수 없다.

요구사항은 두 가지다:

1. 상단 네비게이션에 언어 전환 인터페이스를 두고 **UI가 그 언어로 동작한다**
2. 선택한 언어에 따라 **생성되는 문서·프로토타입·채팅이 그 언어로** 진행된다

## 이미 겪은 실패 — 이 스펙의 전제

커밋 `7f33652 fix(discovery): PR/FAQ 질문이 영어로 남는 문제 (상류 룰
오버라이드)`가 정확히 이 문제였고, `discovery-config/CLAUDE.md:34-37`이 원인을
기록해 뒀다:

> 원인은 두 지시가 반대를 말하기 때문이다 — core-workflow의 "모든 문서작성은
> 한국어"와, 템플릿 바로 앞의 `**CRITICAL**: Use the format exactly as
> defined below. Do NOT deviate from this structure.` 후자가 더 강조돼 있고
> 맥락도 가까워 이긴다.

**언어 지시가 두 레벨에 동시에 있으면 어느 쪽이 이길지 예측할 수 없다.** 그리고
현재 언어 지시는 정확히 그렇게 흩어져 있다:

| 위치 | 범위 | 프로젝트별 가변? |
|---|---|---|
| `rule/aiplc-rules/aws-aiplc-rules/core-workflow.md:3` | 매 턴 워크스페이스 `CLAUDE.md`로 복사(`place_rules`) | 가능 |
| `discovery-config/CLAUDE.md:1-56` | `CLAUDE_CONFIG_DIR` — **전 프로젝트 공유** | 불가 |
| `proto-config/CLAUDE.md:1` | `CLAUDE_CONFIG_DIR` — **전 프로젝트 공유** | 불가 |
| `proto/session.py`의 프롬프트 3종 | 세션별 Python 문자열 | 가능 |

`setting_sources=["user", "project"]`에서 `user`는 공유 config dir이고
`project`는 워크스페이스다(`claude_driver.py:460`, `proto/builder.py:190`).
따라서 **프로젝트별 언어는 반드시 `project` 레벨로만 흘러야 하고, 공유 config
dir의 언어 지시는 제거돼야 한다.** 남겨두면 영어 프로젝트에서 두 지시가
충돌하고, 그 실패는 조용하다 — 문서 절반이 영어로 나와도 에러는 없다.

**영어가 상류 룰의 원래 언어라는 점이 유리하게 작용한다.** 상류 AI-PLC 룰의
문서 양식은 영어로 쓰여 있고, `discovery-config/CLAUDE.md`의 §"문서 양식의 영어
문구는 번역해서 쓴다"는 그것을 한국어로 옮기라는 오버라이드다. 영어 모드에서는
**그 절을 붙이지 않으면 끝난다** — 오버라이드할 것이 없다.

## 설계

### 1. 두 개의 독립된 언어 채널

서로 참조하지 않는다.

```
① UI 언어 (사용자별)              ② 생성물 언어 (프로젝트별)
   pf_lang 쿠키                      project.json 매니페스트
       ↓                                  ↓
   layout.tsx (서버, <html lang>)     ProjectRegistry.get_language()
       ↓                                  ↓
   LocaleProvider (클라이언트)        ├→ place_rules() → 워크스페이스 CLAUDE.md
       ↓                              ├→ proto/session.py first_prompt()
   t("key") — 65개 파일               ├→ survey/builder.py 프롬프트
                                      └→ survey/store.py 리포트 생성
```

**왜 하나로 묶지 않는가.** UI 언어는 언제든 되돌릴 수 있지만 생성물 언어는 그럴
수 없다 — 이미 생성된 `aiplc-docs/**`와 CLI 트랜스크립트가 이전 언어로 남기
때문이다. 워크숍 중간에 전환하면 한 프로젝트 안에 한국어 문서와 영어 문서가
섞이고, 그 상태는 재현도 설명도 어렵다. 그래서 UI는 사용자별 즉시 전환,
생성물은 프로젝트 생성 시 1회 결정으로 나눈다.

②는 `model_id`가 이미 깐 길을 그대로 쓴다(스펙 `2026-08-01-per-project-model-
selection`). 새 배관이 아니라 기존 배관에 필드 하나를 추가하는 것이다.

### 2. 생성물 언어 — 매니페스트에 복사한다

`project.json`에 `language` 필드를 추가한다:

```json
{"project_id": "pilot2", "name": "...", "created_at": "...",
 "model_id": "global.anthropic.claude-opus-5", "language": "ko"}
```

값은 `"ko" | "en"`. `model_id`와 같은 규율을 따른다:

- **미지정은 명시적 `null`로 기록한다.** 키를 빼면 '구 매니페스트'와 '언어를
  고르지 않은 새 프로젝트'를 구별할 수 없다(`project_store.write_manifest`의
  주석이 `model_id`에 대해 같은 판단을 기록해 뒀다).
- **폴백은 `ko`다.** 구 매니페스트로 복원된 프로젝트는 전부 한국어로 만들어진
  것이므로, `None`을 `ko`로 읽는 것이 사실에 맞다. `model_id`의 폴백이 env
  기본값인 것과 같은 이유 — 기능 이전에 만든 것이 계속 같은 동작을 해야 한다.
- **검증은 두 값만 허용한다.** 임의 문자열이 매니페스트에 들어가면 `place_rules`가
  어느 지시 블록을 붙일지 결정할 수 없다. `_validate_model_id`와 같은 자리에서
  400으로 거절한다.

`restore_projects()`의 반환은 4-tuple에서 5-tuple이 된다
(`(pid, name, created_at, model_id)` → `+ language`).
`ProjectRegistry`에 `_language` 맵과 `get_language(pid)`를 추가하고,
`get_language`는 미등록/미지정에 `"ko"`를 반환한다 — `get_model_id`가 `None`을
반환하는 것과 다른 선택이다. 언어에는 "없음"이라는 유효 상태가 없다(어떤 언어로든
써야 한다). 호출부가 폴백을 반복하지 않게 레지스트리가 확정한다.

### 3. 언어 지시의 단일 출처 — `place_rules`가 조립한다

이 스펙에서 **가장 위험한 부분**이다(§8 참조).

현재 `workspace_rules.place_rules`는 `core-workflow.md`를 워크스페이스
`CLAUDE.md`로 그대로 복사한다. 이것을 바꾼다:

1. `core-workflow.md:3`의 언어 헤더 한 줄을 **삭제한다.** 상류 룰 파일에서
   언어를 빼면 그 파일은 언어 중립이 된다.
2. `rule/aiplc-rules/language/ko.md`, `en.md`를 새로 둔다. `ko.md`는 현재
   `core-workflow.md:3`의 지시 + `discovery-config/CLAUDE.md`의 §"문서 양식의
   영어 문구는 번역해서 쓴다" 전체를 담는다. `en.md`는 영어로 쓰라는 지시만
   담는다 — 번역 오버라이드 절이 없다.
3. `place_rules(workspace, rules_dir, language)`가 워크스페이스 `CLAUDE.md`를
   **`language/{lang}.md` + `core-workflow.md`** 순서로 이어 붙여 쓴다.

언어 지시를 **앞에** 두는 이유는 `discovery-config/CLAUDE.md:34-37`이 기록한
그 실패의 반대편이다. 그 실패에서는 "맥락이 가까운" 템플릿의 CRITICAL이
이겼다. 여기서는 언어 지시가 문서 양식보다 먼 것이 아니라 **문서 전체의 전제로
맨 앞에 오고**, `ko.md`가 그 CRITICAL을 어떻게 읽어야 하는지까지 명시적으로
설명한다(현재 오버라이드 절이 하는 일 그대로).

**공유 config dir의 `CLAUDE.md` 두 개에서 언어 지시를 삭제한다.**
`discovery-config/CLAUDE.md`는 §"문서 양식의 영어 문구는 번역해서 쓴다" 전체가
나가고(그 내용은 `language/ko.md`로 이동한다), `proto-config/CLAUDE.md`는 1행의
`모든 대화는 한국어로 진행`이 나간다.

**비ASCII 표기 규약(`\uXXXX` 금지)은 남긴다.** 언어 규약처럼 보이지만 아니다 —
어느 언어로 쓰든 툴 호출 JSON에 유니코드 이스케이프를 쓰지 말라는 **인코딩**
규약이고, 영어 프로젝트에서도 한국어 파일명·기존 문서를 다룰 때 필요하다.

남는 것(도구 사용 규약, 포트 금지, Bedrock 샘플링 파라미터, basePath/
trailingSlash)도 전부 언어 중립이므로 그대로 둔다.

`place_rules`는 멱등하고 매 턴 호출된다. 현재 `_copy_if_changed`가 크기 비교로
건너뛰는데, 조립한 `CLAUDE.md`는 언어가 바뀌면 크기도 바뀌므로 같은 최적화가
유효하다. 다만 조립 결과는 원본 파일이 아니므로 `CLAUDE.md`만 크기 비교
대상에서 빼고 항상 쓴다 — 파일 하나 쓰기는 싸고, 언어가 바뀌었는데 크기가
우연히 같은 경우를 추론으로 배제하고 싶지 않다.

### 4. 프로토타입 — 빌드 에이전트와 생성되는 앱

**빌드 에이전트의 대화 언어**는 `proto/session.py`의 프롬프트 3종
(`_plan_prompt`, `_resume_prompt`, `_handoff_prompt`, 그리고
`_missing_output_prompt`)이 결정한다. 이 프롬프트는 Python f-string이므로
언어별 판을 두고 `PrototypeSession`이 프로젝트 언어로 고른다.
`proto_session_factory`가 이미 `project_id`를 알고 `project_model()`을
호출하므로, 같은 자리에서 `registry.get_language()`를 읽어 넘긴다.

프롬프트가 길어서(plan 프롬프트만 35줄) 언어별 전문을 두 벌 유지하는 비용이
크다. 그러나 이 프롬프트는 **유일한 브레이크**다 —
`first_prompt`의 docstring이 기록하듯 빌더는 `bypassPermissions`로 돌아
Write/Edit이 자동 승인되고, "계획만 세우고 빌드하지 마"를 이 텍스트 밖에서
강제할 방법이 없다. 조립이나 치환으로 문장을 쪼개면 그 지시의 강도가 어느
언어에서 약해졌는지 알 수 없게 된다. **두 벌을 각각 완성된 문장으로 유지한다.**

`build_complete` 도구 설명(`proto/tools.py`)과 거부 메시지도 같은 이유로 언어별
판을 둔다 — 도구 설명은 모델이 읽는 프롬프트다.

**생성되는 앱의 UI 언어**는 프롬프트로 지시한다(빌드 규칙 절에 한 줄 추가:
프로토타입 화면의 문구를 프로젝트 언어로 쓸 것). 생성된 앱 자체에 i18n을
넣는 것은 범위 밖이다 — 프로토타입은 단일 언어 데모다.

### 5. UI i18n — 라이브러리를 쓰지 않는다

`next-intl`은 URL 세그먼트 라우팅(`/ko/...`, `/en/...`)을 전제한다. 그러면
`middleware.ts`의 `gateDecision` 경로 판정, `lib/auth/safeNext.ts`,
`lib/api/rewriteLocation.ts`, 그리고 `/api/proto/{pid}/{slug}/` 프록시
프리픽스가 전부 로케일 세그먼트를 다뤄야 한다. `trailingSlash`/`basePath`
리다이렉트 루프를 이미 겪은 프록시 계층을 언어 때문에 다시 건드릴 이유가 없다
(`proto-config/CLAUDE.md`의 §trailingSlash). **쿠키 기반, 경로 불변.**

```
frontend/lib/i18n/
  ko.ts, en.ts      — 평면 키 → 문자열 (중첩 없음)
  index.ts          — Locale 타입, DEFAULT_LOCALE, LANG_COOKIE, 딕셔너리 조회
  provider.tsx      — LocaleProvider + useT() + useLocale()
```

`server.ts`는 없다 — `app/layout.tsx`가 `cookies()`를 직접 부르는 유일한
지점이고, 파일 하나를 위한 모듈은 만들지 않는다.

키는 **평면**으로 둔다. 중첩 객체는 타입 추론이 깊어지고 `t("a.b.c")` 형태의
문자열 경로를 쓰면 타입 검사를 잃는다. `en.ts`가 `ko.ts`의 키 집합과 정확히
같음을 타입으로 강제한다(`Record<keyof typeof ko, string>`) — 누락된 키가
런타임 `undefined`가 아니라 컴파일 에러로 잡힌다.

**서버/클라이언트 경계 — 실제로는 경계가 하나뿐이다.**

`"use client"`가 없는 컴포넌트가 26개 있지만(`AppHeader`, `StageTimeline`,
`AiMessage`, `DocTree` 등) **그중 서버에서 렌더되는 것은 하나도 없다.** 전수
확인한 결과, `AppHeader`를 그리는 7개 페이지가 전부 `"use client"`이고
(`app/page.tsx`, `admin/users`, `admin/models`, `dashboard`, `workspace`,
`review`, `prototypes`), 나머지도 모두 그 트리 아래에서만 임포트된다. Next.js는
클라이언트 컴포넌트가 임포트한 것을 클라이언트 번들에 넣으므로, 이 26개는
**`"use client"`를 안 쓴 클라이언트 컴포넌트**다.

서버에서 실제로 렌더되는 것은 셋뿐이고 셋 다 UI 문자열이 없다:
`app/layout.tsx`, 그리고 `redirect()`만 하는 `questions/page.tsx`·
`canvas/page.tsx`.

따라서 **`getT()`(서버용 경로)를 만들지 않는다.** 필요한 것은 하나다:

- `app/layout.tsx`가 `cookies()`로 로케일을 읽어 `<html lang>`에 넣고
  `LocaleProvider`에 초기값으로 내려준다. 여기가 유일한 서버 측 로케일 판독
  지점이다.
- 나머지 전부 — 26개 포함 — 는 `useT()`를 쓴다. `"use client"`가 없는 파일에는
  **추가한다**(이미 클라이언트 컴포넌트이므로 동작 변화 없이 훅 사용이
  허용된다).

이것이 §7의 테스트 부담을 없앤다: `cookies()`를 부르는 코드가 `layout.tsx`
하나이고 그 파일은 컴포넌트 테스트 대상이 아니므로, jsdom에서 `cookies()`가
없어 깨지는 테스트가 애초에 생기지 않는다.

**언어 스위치.** `AppHeader`에 꽂는 `LanguageSwitcher`를 별도 파일로 만든다 —
`AppHeader` 자체가 이미 클라이언트 컴포넌트지만, 스위치는 쿠키 쓰기와
`router.refresh()`라는 별개 책임이라 분리한다(`UserMenu`가 같은 형태다).
쿠키를 쓰고 `router.refresh()`를 호출하면 `layout.tsx`가 새 로케일로 다시
렌더되어 `<html lang>`과 Provider 초기값이 갱신된다.

`localStorage`가 아니라 쿠키인 이유는 **`layout.tsx`가 서버에서 읽어야** 하기
때문이다 — `<html lang>`을 첫 페인트에 맞추려면 서버가 알아야 하고,
`localStorage`는 서버에서 보이지 않아 깜빡임이 생긴다. httpOnly가 아니다 —
스위치가 클라이언트에서 써야 하고, 보안 값이 아니다.

**폰트는 그대로 둔다.** `layout.tsx:5`의 `Noto_Sans_KR`은 `subsets: ["latin"]`
이라 라틴 문자만 가져온다(한글은 시스템 폴백). 영어 UI에서도 문제없다.

**헤더의 언어 배지.** 프로젝트가 선택된 화면에서는 모델 배지 옆에 그 프로젝트의
생성물 언어를 **읽기 전용**으로 표시한다. UI 스위치와 값이 다를 수 있고, 그것이
정상이라는 것을 화면에서 드러내야 한다 — 영어 UI로 한국어 프로젝트를 열면
문서는 한국어로 나온다.

### 6. 한국어 리터럴에 결합된 로직

번역만으로 끝나지 않고 **깨지는** 지점이다. 넷 중 셋은 이미 언어 중립화 선례가
있고, 하나는 실제 결함이다.

**(a) 승인 게이트 — 유일한 진짜 결함**

`app/projects/[projectId]/review/page.tsx:128`이 턴 텍스트로 `"승인"`을 보내고,
`lib/approvalState.ts:17`이 감사 로그의 `user_input`을 `/^\s*승인\s*$/`로
판정한다. 상류 룰은 "user explicitly approves"만 요구하고 **키워드를 정의하지
않는다**(`envision.md:412`, `product-strategy.md:154`, `go-to-market.md:160`) —
즉 `"승인"`은 우리가 정한 프로토콜이다. 영어 UI에서 영어 라벨을 누르면 영어
텍스트가 가고, 정규식이 인식하지 못해 **게이트가 영원히 열리지 않는다.**

수정: **턴 텍스트를 프로젝트 언어의 승인 단어로 보내고, 판정은 두 언어를 다
받는다.**

불투명 마커(`[APPROVED]` 같은 것)를 쓰지 않는 이유는 이 텍스트가 기계 신호가
아니기 때문이다. `sendTurn`은 `postMessage`로 이것을 **에이전트에게 보내고**
(`review/page.tsx:89-93`), 그 턴은 트랜스크립트와 채팅 히스토리에 사용자
말풍선으로 남는다. 에이전트가 승인으로 이해해야 하고 사람이 읽어야 한다. 반면
프로젝트 언어의 승인 단어는 에이전트가 이미 그 언어로 대화하고 있으므로 추가
프롬프트 지원 없이 통한다.

**UI 언어가 아니라 프로젝트 언어인 것이 요점이다.** 영어 UI로 한국어 프로젝트를
열어 승인하면, 대화는 한국어로 진행되고 있으므로 `승인`이 가야 한다. 버튼
라벨만 UI 언어로 번역된다(영어 UI에 "Approve" 버튼, 한국어 턴).

판정 정규식은 `/^\s*(승인|Approved)\s*$/i`로 두 언어를 받는다 — 기존 한국어
감사 로그가 계속 인식되어야 하고, `parsers/audit.py:42`가 `사용자 입력|User Raw
Input`을 둘 다 받는 것과 같은 규율이다. 보낼 단어와 판정 정규식은 **같은
모듈에서 나오게** 묶어, 한쪽만 바뀌어 게이트가 조용히 안 열리는 일을 막는다.

`isDocumentChange`의 `/수정|revise|revision|재작성|갱신|업데이트|update/i`는
이미 영어 대안이 있어 그대로 둔다.

**(b) `session_history.py` — 접두사 결합**

`agent/strands_tools.py:85`가 `f"사용자 답변: {json}"`을 만들고
`session_history.py:109·149`가 그 접두사를 제거한다. 생산자와 소비자가 같은
리포 안에 있으므로 접두사를 언어 중립으로 바꾸고 양쪽을 함께 고친다. 기존
트랜스크립트 호환을 위해 제거는 두 형태 모두 시도한다.

사용자에게 보이는 `"답변 제출"` / `"답변 제출 — 1: A · 2: B"` 요약 문구도
백엔드가 만드는데(`session_history.py:116-120·153-158`, 두 경로가 같은 문구를
각자 만든다), 백엔드는 UI 언어를 모른다. **문구를 프론트로 옮긴다:**
`HistoryItem`에 `answers: Record<string, string> | null`을 추가하고, 백엔드는
파싱한 답변 dict를 그대로 넘긴다. 프론트가 `answers`가 있는 항목을 UI 언어의
라벨 + `번호: 값 · …`으로 렌더한다.

**라이브 경로의 `answerSummary`를 재사용하지 않는다.** 그 함수는 선택지 문자를
옵션 텍스트로 펼치기 위해 `QuestionFile`을 요구하는데(`answerSummary.ts:71`),
복원 경로에는 그것이 없다 — 트랜스크립트에는 답변 dict만 남아 있다. 복원된
말풍선이 라이브보다 덜 자세한 것은 현재도 같고(현재도 `번호: 값`까지만
보여준다), 이 스펙에서 바꾸려는 것은 **자세함이 아니라 언어**다.

`text`는 그대로 채워 보낸다 — `answers`를 모르는 구 프론트가 빈 말풍선을
띄우지 않게 하는 폴백이다.

**(c) `parsers/audit.py`, `parsers/questions.py` — 손대지 않음**

`사용자 입력|User Raw Input`과 `추천|recommended`는 이미 양쪽을 받는다. 영어
프로젝트에서 에이전트가 영어 헤딩을 쓰면 그대로 파싱된다.
`parsers/state.py`의 `**Project Type**`/`**Current Stage**`는 언어 중립이다.

**(d) 백엔드 HTTP 에러 14곳 → 코드로 반환**

`admin_users.py`(4), `models.py`(4), `prototypes.py`(3), `surveys_public.py`(2),
`projects.py`(1)의 `detail=` 한국어 문구다. 백엔드는 UI 언어를 모른다 —
`app/api/[...path]/route.ts`의 `filterHeaders`가 `Accept-Language`를 전달하지
않고, 전달하게 만들어도 브라우저 값이 들어와 UI 스위치와 어긋난다.

**`detail`을 안정적인 코드 문자열로 바꾸고 프론트 딕셔너리가 문구를 소유한다.**
UI 언어의 단일 출처가 이미 프론트에 있으므로 백엔드에 두 번째 번역 시스템을
만들지 않는다. 프론트가 모르는 코드는 원문을 그대로 표시해 폴백한다 — 새 에러가
추가됐을 때 빈 화면이 아니라 코드가 보이는 편이 낫다.

**(e) `survey/store.py` — 리포트 마크다운 24줄**

`aiplc-docs/**`에 생성되는 **산출물**이므로 UI 언어가 아니라 **프로젝트 언어**를
따른다. 에이전트 프롬프트가 아니라 Python이 직접 만드는 문서라서 언어별 라벨
테이블을 백엔드에 둔다. (d)의 "백엔드에 번역 시스템을 만들지 않는다"와
모순되지 않는다 — 여기는 UI 문구가 아니라 문서 생성기이고, 프로젝트 언어는
이미 백엔드가 아는 값이다.

`survey/builder.py`의 설문 생성 프롬프트도 언어별 판을 둔다(§4의 프로토타입
프롬프트와 같은 이유 — 프롬프트는 조립하지 않는다).

### 7. 테스트

**기본 로케일이 `ko`이므로 기존 테스트는 대부분 그대로 통과한다.** 쿠키가
없으면 `ko`이고, 테스트는 쿠키를 설정하지 않으므로 렌더 결과가 현재와 같다.
베이스라인: `83개 파일 / 664개 테스트 통과`(2026-08-03 측정).

깨지는 것은 없다. `cookies()`를 부르는 코드가 `app/layout.tsx` 하나이고 그
파일은 컴포넌트 테스트 대상이 아니다(§5). 신규 테스트만 추가한다 —
`LanguageSwitcher`, 그리고 아래 영어 렌더 항목들.

**`useT()`가 Provider 밖에서 불릴 때 `ko`로 폴백한다.** 기존 테스트 535건은
컴포넌트를 Provider로 감싸지 않고 `render()`하므로, 이 폴백이 그 테스트들을
그대로 통과시키는 장치다. 폴백이 없으면 훅이 던지고 전부 깨진다.

**한국어 문자열을 직접 쓰는 단정문 535건은 그대로 둔다.**
`getByText("대시보드")`를 딕셔너리 조회로 바꾸면 "딕셔너리가 자기 자신과 같다"는
무의미한 테스트가 된다. 리터럴로 남겨두면 번역 키를 잘못 연결했을 때 실패하는
진짜 테스트다.

**영어 렌더는 대표 테스트만 추가한다:**
- 스위치가 쿠키를 설정하고 refresh를 호출하는지
- `<html lang>`이 로케일에 따라 바뀌는지
- 헤더 탭이 영어 라벨로 렌더되는지
- `en.ts`의 키 집합이 `ko.ts`와 같은지(타입으로 강제되지만 회귀 방지)
- 백엔드 에러 코드가 프론트에서 번역되는지, 모르는 코드는 원문 폴백인지

**백엔드**
- `restore_projects()` 5-tuple + 구 매니페스트(`language` 없음) → `ko`
- `ProjectRegistry.get_language()`가 미등록/미지정에 `"ko"`
- `POST /projects`: `ko`/`en` 허용, 그 외 400, 미지정 허용
- `place_rules(..., language)`가 언어별 지시 블록을 앞에 붙이는지, 두 언어의
  `CLAUDE.md`가 서로 다른지, 멱등한지
- `core-workflow.md`에 언어 지시가 **없음**을 단정 — 이 스펙의 핵심 불변식이고,
  누가 상류 룰을 갱신하며 그 줄을 되살리면 조용히 충돌이 돌아온다
- 공유 config dir의 `CLAUDE.md` 두 개에 언어 지시가 없음을 단정 (같은 이유)
- `first_prompt()` 3종 × 2언어가 각각 언어에 맞는 텍스트인지
- `survey/store.py` 리포트가 프로젝트 언어로 생성되는지
- 승인 판정: `승인`과 `Approved` 둘 다 인식하는지, 그리고 **영어 UI + 한국어
  프로젝트**에서 보내는 턴이 `승인`인지(§6a의 핵심 구분)
- `session_history` 접두사 제거가 신·구 두 형태 모두 동작하는지

### 8. 실행 순서

되돌릴 수 없는 것을 나중에 두고, 각 단계가 독립적으로 배포 가능하도록 배열했다.

| 단계 | 내용 | 위험 |
|---|---|---|
| 1 | i18n 기반(`lib/i18n/`) + `layout.tsx` 배관 + `LanguageSwitcher`. 화면 문자열은 아직 안 건드림 | 낮음 |
| 2 | `language` 필드: 매니페스트 → 레지스트리 → 생성 폼 → 헤더 배지 | 낮음(`model_id` 선례) |
| 3 | 승인 게이트 판정 + `session_history.py` 접두사·요약 | 중간 — 기존 감사 로그/트랜스크립트 호환 필수 |
| 4 | 백엔드 에러 14곳 → 코드화, 프론트 딕셔너리가 문구 소유 | 낮음 |
| 5 | UI 문자열 65개 파일 전수 치환 | 낮음(양이 많음) |
| 6 | `place_rules` 언어별 `CLAUDE.md` 조립 + 공유 config dir에서 언어 줄 제거 | **높음 — 에이전트 동작 변경** |
| 7 | `proto/session.py` 프롬프트 3종 + `survey/builder.py` + `survey/store.py` | 중간 |

**2단계가 3단계보다 앞인 것은 의존성이다.** 승인 턴 텍스트는 프로젝트 언어를
읽어야 하므로(§6a) 레지스트리에 `get_language()`가 먼저 있어야 한다. 반대
순서로 하면 3단계가 UI 언어를 임시로 읽고 2단계에서 되돌리는 일이 생긴다.

1단계와 2단계 사이에는 의존성이 없다 — 1은 프론트, 2는 백엔드 + 생성 폼이다.
`LanguageSwitcher`는 1단계에서 이미 동작하지만 번역할 문자열이 없어 화면 변화가
없다. 이것이 의도다: 배관과 치환을 분리해, 5단계에서 무언가 깨지면 원인이
딕셔너리 연결이지 배관이 아님을 안다.

**6단계가 가장 위험하다.** 이유는 `discovery-config/CLAUDE.md`가 기록한 그
실패(두 레벨의 언어 지시 충돌)를 다시 만들 수 있고, **실패가 조용하다는**
점이다 — 문서 절반이 영어로 나와도 에러는 없다. 그래서 6·7단계는 워크숍 전에
한국어/영어 프로젝트를 각각 하나씩 실제로 돌려 산출물 언어를 눈으로 확인하는
검증이 필요하다. 자동 테스트로는 잡히지 않는다: 테스트는 `CLAUDE.md`가 어떻게
조립됐는지만 확인할 수 있고, 모델이 그것을 따랐는지는 확인할 수 없다.

검증 항목(수동): PR/FAQ의 `Q:` 질문 문구, `product-strategy.md`와
`go-to-market.md`의 표 헤딩·라벨, 섹션 헤딩, 그리고 채팅 말풍선. 이 네 곳이
`7f33652`에서 실제로 어긋났던 지점이다.

## 범위 밖

- **이미 생성된 문서의 사후 번역** — 프로젝트 언어는 생성 시 1회 결정이고,
  기존 산출물은 만들어진 언어로 남는다
- **프로젝트 생성 후 생성물 언어 변경** — §1의 이유(트랜스크립트와 문서가
  섞인다). `model_id`가 같은 이유로 생성 시 1회 결정인 것과 나란하다
- **생성된 프로토타입 앱의 i18n** — 프로토타입은 단일 언어 데모다(§4)
- **공개 설문 페이지의 응답자 언어 선택** — 설문 문항 자체가 프로젝트 언어로
  생성되므로 응답 화면도 그 언어를 따른다. 문항은 한국어인데 UI만 영어인
  화면은 응답자에게 더 나쁘다
- **`StrandsDriver`의 언어 처리** — 폴백 경로, 워크숍 후 삭제 예정
  (`2026-08-01-per-project-model-selection`이 `model_id`에 대해 같은 판단)
- **제3언어 추가** — 딕셔너리 구조는 확장 가능하지만, `ko.md`/`en.md`의 언어
  지시와 프롬프트 2벌 유지 비용이 언어 수에 비례한다. 3번째 언어가 필요해지면
  프롬프트를 조립으로 바꾸는 별개 결정이 필요하다
