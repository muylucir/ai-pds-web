# 보기 선택 + 선택적 부연 설명 설계

날짜: 2026-07-21
상태: 승인됨 (구현 대기)

## 배경과 문제

방법론 룰(question-format-guide.md)은 `[Answer]:` 태그 뒤에 letter와 자유 서술을
함께 적는 것을 전제한다 — 예: 문서 승인 질문에서 `B) 대체로 좋지만 몇 군데 수정 필요
(수정사항은 [Answer]: 뒤에 설명)`. Kiro/Claude Code 사용자는 질문 `.md` 파일을 직접
편집해 `[Answer]: B, 헤드라인을 X로 바꿔주세요`처럼 쓸 수 있다.

Pathfinder는 파일 직접 편집이 없다. 답변은 `ask_questions` 도구 → interrupt →
`send_answers(answers: dict[str, str])`로만 흐르고, 폼은 질문당 문자열 하나(letter
또는 Other 자유텍스트)만 제출한다. **일반 보기를 고르면 letter만 전달되어, "수정
필요" 같은 보기의 실제 수정 내용을 적을 자리가 없다** (스크린샷:
files/20260721-fix1.png — 보기 B 선택 시 부연 입력란 부재).

해결 방향(사용자 결정): **모든 일반 보기에 선택적 부연 설명을 허용** — Kiro/Claude
Code에서의 자연스러운 `[Answer]: letter + 설명` 경험을 Pathfinder 폼에 재현한다.

## 값 계약 (승인됨)

- 부연이 있는 일반 보기 답변: **`"B: <설명>"`** — letter + `": "`(콜론+공백) + 설명.
- 부연이 없으면 기존대로 **`"B"`**.
- Other(X)는 기존대로 **순수 자유텍스트** (콜론 규약 없음).
- multi-select는 기존대로 **`"A,C"`** (부연 미지원 — 아래 "범위" 참고).
- `answers: dict[str, str]` 타입, `[Answer]:` 한 줄 직렬화 포맷, 라우트/runner/driver/
  `ask_questions` 도구 시그니처 **전부 불변**. `[Answer]: B: 헤드라인을 X로 수정`으로
  자연스럽게 파일에 기록된다.

**파싱 규약** (표시·복원용): 값의 첫 `": "` 앞 토큰이 해당 질문의 알려진 non-Other
letter이면 `letter + 부연`으로 해석. 아니면 전체를 Other 자유텍스트로 해석.
(사용자 부연에 `": "`가 또 나와도 첫 구분자만 분리하므로 안전. 부연이 letter 한
글자로 시작하는 경우와의 모호성은 "첫 토큰이 정확히 letter"일 때만 분해하므로 없음
— 예: `"B: ..."`는 분해되지만 `"Broker: ..."`는 Other 텍스트.)

## UI 동작 (QuestionCard, 승인됨)

- **single-select에서 일반 보기(A/B/C…)를 선택하면 그 보기 카드 아래에 "부연 설명
  (선택)" 텍스트박스가 펼쳐진다.** 입력 시 `"B: <설명>"`, 비우면 `"B"`.
- 다른 보기로 바꾸면 이전 부연은 버려진다(보기별 독립, 상태는 선택된 보기에만 존재).
- Other(X)는 기존 그대로: 항상 텍스트박스, 값은 순수 자유텍스트.
- **복원 렌더**: 폼이 저장된 답변 `"B: <설명>"`으로 열리면 보기 B 선택 + 부연칸에
  설명을 채워 보여준다. 첫 토큰이 letter가 아니면 기존처럼 Other 자유텍스트로 렌더.
- 기존 결함 수정과의 공존: Other 모드는 명시적 state(`otherActive`)로 추적한다(값
  공간 충돌 수정, 같은 날 선행 작업). 부연은 "보기 선택 모드"에서만 존재하므로
  Other free-text와 섞이지 않는다. 부연 텍스트박스도 라디오 `<label>` 밖에 두어
  포커스/첫 글자 유실 회귀를 만들지 않는다.

## 범위 결정: multi-select 제외 (YAGNI)

multi 값은 `"A,C"` 콤마 조인이라 `"A,C: <설명>"`은 파싱 모호성을 만든다. 방법론상
"수정 필요 + 설명" 패턴은 승인/리뷰형 single-select 질문에서 나오고, multi에서
부연이 필요하면 Other를 쓰면 된다. multi 부연은 실제 수요가 생길 때 별도 설계.

## 에이전트 쪽

- 코드 규약 변경 없음. `QUESTIONS_SCHEMA_HINT`(backend/pathfinder/agent/tools.py)에
  한 줄 추가: 일반 보기 답변은 `letter` 또는 `letter: 부연설명` 형태로 돌아온다 —
  에이전트가 부연을 놓치지 않고 반영하게 한다.

## 데이터 흐름 (무변경 확인)

- `QuestionForm` 제출(문자열 dict) → `GET /projects/{pid}/answers/stream` →
  `AgentRunner.send_answers` → `StrandsDriver.run_answers`(interruptResponse) →
  `ask_questions` 도구 반환 `"사용자 답변: {...}"` — 전 구간 무변경.
- `PUT /projects/{pid}/questions/{name}`(파일 답변 저장)도 무변경 —
  `serialize_answers`가 `[Answer]: B: <설명>`을 그대로 쓴다.
- 히스토리 요약(session_history.py `"답변 제출 — 1: B: …"`)도 그대로 동작.

## 테스트

- **QuestionCard 유닛** (frontend/components/questions/QuestionCard.test.tsx):
  - 일반 보기 선택 시 부연칸이 펼쳐지고, 입력하면 `"B: <설명>"` 제출.
  - 부연을 비우면 `"B"` 제출 (콜론 규약 미부착).
  - 보기 변경 시 이전 부연 초기화.
  - 복원: value `"B: 헤드라인 수정"` → 보기 B checked + 부연칸에 "헤드라인 수정".
  - 복원: 첫 토큰이 letter가 아닌 값(`"Broker: ..."` 포함) → Other 자유텍스트 렌더.
  - Other(X)·multi-select 동작 무영향(기존 테스트 유지).
  - 부연 텍스트박스가 라디오 `<label>` 안에 중첩되지 않음(포커스 회귀 가드).
- **백엔드**: 무변경 — 기존 스위트 그린 유지 확인만.

## Open Questions

없음.
