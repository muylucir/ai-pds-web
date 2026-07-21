# 보기 선택 + 선택적 부연 설명 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** single-select 질문에서 일반 보기(A/B/C…)를 고르면서 선택적 부연 설명을 함께 제출할 수 있게 한다 — 값은 `"B: <설명>"` 단일 문자열.

**Architecture:** 값 계약·백엔드·에이전트 도구는 전부 불변. `QuestionCard`(controlled component)가 `"letter: note"` 형태를 분해/조립하는 로직과 선택된 보기 아래 펼쳐지는 부연 텍스트박스를 얻는다. 에이전트 시스템 프롬프트 힌트(`QUESTIONS_SCHEMA_HINT`)에 답변 형태 한 줄을 추가한다.

**Tech Stack:** React 19 (Next.js 15), Vitest + Testing Library (frontend), pytest (backend hint test).

## Global Constraints

- 값 계약: 부연 있는 일반 보기 = `"B: <설명>"` (letter + `": "` + 설명), 부연 없으면 `"B"`, Other(X) = 순수 자유텍스트, multi-select = `"A,C"` (부연 미지원). `answers: dict[str,str]`·`[Answer]:` 포맷·라우트/runner/driver 불변.
- 파싱 규약: 값의 첫 `": "` 앞 토큰이 해당 질문의 알려진 non-Other letter일 때만 letter+note로 분해. 아니면(예: `"Broker: ..."`) 전체를 Other 자유텍스트로 해석.
- 회귀 가드 유지: Other 모드는 명시적 state(`otherActive`)로 추적(값 공간 충돌 수정), 어떤 textarea도 라디오/체크박스 `<label>` 안에 중첩 금지(포커스/첫 글자 유실 수정). 기존 QuestionCard 테스트 12개는 전부 그린 유지.
- 커밋 메시지 말미: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

## File Structure

- Modify: `frontend/components/questions/QuestionCard.tsx` — `splitLetterNote` 헬퍼 + 선택 보기 아래 부연 textarea (핵심 변경, 이 파일 하나에 격리)
- Modify: `frontend/components/questions/QuestionCard.test.tsx` — 부연 동작 테스트 추가
- Modify: `backend/pathfinder/agent/tools.py` — `QUESTIONS_SCHEMA_HINT`에 답변 형태 한 줄
- Modify: `backend/tests/test_agent_tools.py` — 힌트 문구 테스트

`QuestionForm`/백엔드 파서/라우트는 무변경 — 값이 문자열 dict로 그대로 흐른다.

---

### Task 1: QuestionCard — letter + 부연 설명 UI

**Files:**
- Modify: `frontend/components/questions/QuestionCard.tsx`
- Test: `frontend/components/questions/QuestionCard.test.tsx`

**Interfaces:**
- Consumes: 기존 `QuestionCard({ question, value, onChange })` props — 시그니처 불변.
- Produces: `onChange`가 `"B"` 또는 `"B: <설명>"`을 내보냄. 모듈 내부 헬퍼 `splitLetterNote(value: string, letters: string[]): { letter: string; note: string } | null` (export 불필요 — 컴포넌트 전용).

- [ ] **Step 1: 실패 테스트 작성**

`frontend/components/questions/QuestionCard.test.tsx` 맨 아래에 추가 (기존 `Harness`/`q1`/`MULTI_Q` 재사용 — q1의 non-Other 보기: A "Niche Specialist", B "플랫폼(Platform)"):

```tsx
describe("QuestionCard — 보기 부연 설명 (letter + note)", () => {
  // 스펙(2026-07-21-option-annotation-design.md): 일반 보기를 고르면 그 보기
  // 아래 '부연 설명 (선택)' 입력란이 펼쳐지고, 입력하면 "B: <설명>" 단일
  // 문자열로 제출된다. Kiro/Claude Code의 "[Answer]: letter + 설명" 경험을
  // 파일 편집 없는 Pathfinder 폼에 재현하는 값 계약.

  it("보기를 선택하면 부연 설명 입력란이 그 보기 아래 펼쳐진다", async () => {
    const user = userEvent.setup();
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={vi.fn()} />);
    // 선택 전에는 부연 입력란 없음
    expect(screen.queryByLabelText(/보기 B 부연 설명/)).toBeNull();
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    expect(screen.getByLabelText(/보기 B 부연 설명/)).toBeInTheDocument();
    // 다른(미선택) 보기에는 입력란이 없음
    expect(screen.queryByLabelText(/보기 A 부연 설명/)).toBeNull();
  });

  it("부연을 입력하면 'B: <설명>' 형태로 제출된다", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={spy} />);
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    await user.type(screen.getByLabelText(/보기 B 부연 설명/), "헤드라인을 X로 수정");
    expect(spy).toHaveBeenLastCalledWith("B: 헤드라인을 X로 수정");
  });

  it("부연을 전부 지우면 letter만 남는다", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={{ ...q1, multi_select: false }} initial="B: 임시" spy={spy} />);
    await user.clear(screen.getByLabelText(/보기 B 부연 설명/));
    expect(spy).toHaveBeenLastCalledWith("B");
  });

  it("보기를 바꾸면 이전 부연이 초기화된다", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={{ ...q1, multi_select: false }} initial="B: 수정 필요" spy={spy} />);
    await user.click(screen.getByText(/Niche Specialist/));
    expect(spy).toHaveBeenLastCalledWith("A");           // 부연 없이 letter만
    expect(screen.getByLabelText(/보기 A 부연 설명/)).toHaveValue("");
  });

  it("저장된 'B: 설명' 값 복원 시 보기 B 선택 + 부연이 채워진다", () => {
    render(<Harness question={{ ...q1, multi_select: false }} initial="B: 헤드라인 수정" spy={vi.fn()} />);
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    const radioB = radios.find((r) => r.value === "B")!;
    expect(radioB.checked).toBe(true);
    expect(screen.getByLabelText(/보기 B 부연 설명/)).toHaveValue("헤드라인 수정");
    // Other 텍스트박스는 비어 있어야 함(자유텍스트로 오인 금지)
    expect(screen.getByLabelText(/기타 답변 직접 입력/)).toHaveValue("");
  });

  it("'Broker: ...' 같은 값(첫 토큰이 letter가 아님)은 Other 자유텍스트로 복원된다", () => {
    render(<Harness question={{ ...q1, multi_select: false }} initial="Broker: 중개 모델" spy={vi.fn()} />);
    expect(screen.getByLabelText(/기타 답변 직접 입력/)).toHaveValue("Broker: 중개 모델");
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios.filter((r) => r.value === "A" || r.value === "B").every((r) => !r.checked)).toBe(true);
  });

  it("multi-select에는 부연 입력란이 없다", () => {
    render(<QuestionCard question={MULTI_Q} value="A" onChange={vi.fn()} />);
    expect(screen.queryByLabelText(/부연 설명/)).toBeNull();
  });

  it("부연 입력란도 라디오 label 안에 중첩되지 않는다 (포커스/첫 글자 유실 회귀 가드)", async () => {
    const user = userEvent.setup();
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={vi.fn()} />);
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    const note = screen.getByLabelText(/보기 B 부연 설명/);
    expect(note.closest("label")).toBeNull();
  });

  it("부연 첫 글자가 옵션 letter여도 유실되지 않는다 (값 공간 충돌 회귀 가드)", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={spy} />);
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    await user.type(screen.getByLabelText(/보기 B 부연 설명/), "A안과 병합");
    expect(spy).toHaveBeenLastCalledWith("B: A안과 병합");
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npx vitest run components/questions/QuestionCard.test.tsx`
Expected: FAIL — 신규 describe의 테스트들이 `보기 B 부연 설명` 라벨 부재로 실패 (기존 12개는 통과 유지).

- [ ] **Step 3: QuestionCard 구현**

`frontend/components/questions/QuestionCard.tsx` 수정:

(a) 파일 상단(컴포넌트 밖)에 헬퍼 추가:

```tsx
// "B: 부연 설명" 값을 letter와 note로 분해한다. 첫 ": " 앞 토큰이 알려진
// non-Other letter일 때만 분해 — "Broker: ..." 같은 값은 null(전체가 Other
// 자유텍스트). 값 계약: 부연 있는 일반 보기 답변은 "letter: note" 단일 문자열
// (스펙 2026-07-21-option-annotation-design.md).
function splitLetterNote(value: string, letters: string[]): { letter: string; note: string } | null {
  const idx = value.indexOf(": ");
  if (idx === -1) return null;
  const head = value.slice(0, idx);
  return letters.includes(head) ? { letter: head, note: value.slice(idx + 2) } : null;
}
```

(b) 컴포넌트 본문에서 `letterNote` 파생 + `otherActive` 시드 갱신 + `selectedLetter` 확장. 기존 31~37행 블록을 다음으로 교체:

```tsx
  // single-select에서 "B: 부연" 형태를 분해 (multi에는 부연 없음 — 스펙의
  // YAGNI 결정: "A,C: 설명"은 파싱 모호성을 만들고, 승인/리뷰형 질문은
  // single-select로 온다).
  const letterNote = !multi ? splitLetterNote(value, nonOtherLetters) : null;

  // Free-text ("Other") mode is tracked EXPLICITLY, not inferred by comparing
  // `value` against option letters. Inferring it broke free text whose first
  // character happened to equal an option letter: typing "A" made value==="A",
  // which read as "option A selected", flipped out of Other mode, and blanked
  // the textarea — the first char was lost and option A rendered as checked.
  // Seeded from the incoming value's shape: a restored answer that is neither
  // a letter/letter-list NOR a "letter: note" form is free text; thereafter
  // only explicit user actions (picking an option vs. using Other) flip it.
  const [otherActive, setOtherActive] = useState(
    () => value !== "" && !isLetterList(value) && splitLetterNote(value, nonOtherLetters) === null,
  );

  // With Other mode explicit, letter selection is only meaningful when NOT in
  // Other mode. Single-select: the picked letter (plain or the head of a
  // "letter: note" value), or "" in Other mode.
  const selectedLetter =
    !otherActive && !multi ? (nonOtherLetters.includes(value) ? value : (letterNote?.letter ?? "")) : "";
  // 선택된 보기의 부연(없으면 "").
  const note = !otherActive ? (letterNote?.note ?? "") : "";
  // Multi-select: the checked letters, or empty while Other free text is in use.
  const multiSelected = new Set(!otherActive && multi && isLetterList(value) ? value.split(",").filter(Boolean) : []);
```

(c) 일반 보기 렌더를 `<div>`로 감싸고, 선택된 보기(single-select) 아래 부연 textarea 추가. 기존 116~146행(`const checked = ...`부터 일반 보기 `</label>` 반환까지)을 다음으로 교체:

```tsx
          const checked = multi ? multiSelected.has(opt.letter) : selectedLetter === opt.letter;
          return (
            <div key={opt.letter}>
              {/* relative: Other 라벨과 동일한 이유 (sr-only absolute 인풋 가둠) */}
              <label className="relative block cursor-pointer">
                <input
                  type={multi ? "checkbox" : "radio"}
                  name={name}
                  value={opt.letter}
                  className="sr-only peer"
                  checked={checked}
                  onChange={() => (multi ? toggleLetter(opt.letter) : selectLetter(opt.letter))}
                />
                <div
                  className={`flex gap-3 rounded-xl border-2 p-4 hover:border-violet-200 ${
                    checked ? "border-violet-600 bg-violet-50" : "border-slate-200"
                  }`}
                >
                  <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                    {opt.letter}
                  </span>
                  <div>
                    <p className="font-medium">
                      {opt.text}
                      {opt.recommended && (
                        <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 ml-1">★ AI 추천</span>
                      )}
                    </p>
                  </div>
                </div>
              </label>
              {/* 부연 설명(선택): 선택된 보기 아래에만 펼쳐진다. textarea를
                  label 밖 형제로 두는 것 필수 — label 안에 중첩하면 클릭
                  포커스가 sr-only 라디오로 가고 첫 키 입력이 유실된다(동일
                  회귀를 Other에서 이미 수정). 값 계약: 입력 시
                  "letter: note", 비우면 letter만. */}
              {checked && !multi && (
                <textarea
                  aria-label={`보기 ${opt.letter} 부연 설명`}
                  rows={2}
                  value={note}
                  onChange={(e) =>
                    onChange(e.target.value === "" ? opt.letter : `${opt.letter}: ${e.target.value}`)
                  }
                  placeholder="부연 설명 (선택) — 수정 요청·조건·이유가 있으면 적어 주세요"
                  className="mt-2 ml-10 w-[calc(100%-2.5rem)] text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
                />
              )}
            </div>
          );
```

> 주의: `selectLetter`/`toggleLetter`/`activateOther`/Other 블록은 무변경. `selectLetter(letter)`가 `onChange(letter)`를 호출하므로 보기 전환 시 부연은 자연히 버려진다.

- [ ] **Step 4: 통과 확인 (신규 + 기존 전부)**

Run: `cd frontend && npx vitest run components/questions/QuestionCard.test.tsx`
Expected: PASS — 기존 12 + 신규 9 = 21 tests.

- [ ] **Step 5: 전체 프론트 스위트 + 타입체크**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: 전체 그린(기존 208 + 신규 9 = 217), tsc 에러 없음.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/questions/QuestionCard.tsx frontend/components/questions/QuestionCard.test.tsx
git commit -m "feat(frontend): optional per-option annotation — submit 'letter: note' from QuestionCard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: QUESTIONS_SCHEMA_HINT — 답변 형태 안내 한 줄

**Files:**
- Modify: `backend/pathfinder/agent/tools.py:12-21` (`QUESTIONS_SCHEMA_HINT`)
- Test: `backend/tests/test_agent_tools.py`

**Interfaces:**
- Consumes: 없음 (독립 문자열 상수).
- Produces: `QUESTIONS_SCHEMA_HINT`에 "letter: 부연설명" 형태 언급 — 드라이버의 `_CONTACT_ADDENDUM`이 이 상수를 f-string으로 삽입하므로 자동 반영.

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_agent_tools.py`의 `test_schema_hint_mentions_parse_ok_and_multi_select` 아래에 추가:

```python
def test_schema_hint_mentions_letter_note_answer_form():
    # 스펙(option-annotation): 일반 보기 답변은 "B" 또는 "B: 부연설명" 형태로
    # 돌아온다 — 에이전트가 부연을 놓치지 않도록 힌트에 명시되어야 한다.
    assert "부연" in QUESTIONS_SCHEMA_HINT
    assert "'B: " in QUESTIONS_SCHEMA_HINT or '"B: ' in QUESTIONS_SCHEMA_HINT
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_tools.py::test_schema_hint_mentions_letter_note_answer_form -q`
Expected: FAIL — 현재 힌트에 "부연" 없음.

- [ ] **Step 3: 힌트 문구 추가**

`backend/pathfinder/agent/tools.py`의 `QUESTIONS_SCHEMA_HINT` 마지막 문장(`"multi_select 질문의 답변은 'A,C'처럼 콤마로 조인되어 돌아온다."`) 뒤에 이어 붙인다:

```python
QUESTIONS_SCHEMA_HINT = (
    "ask_questions의 questions_file 인자는 반드시 다음 JSON 형태여야 한다: "
    '{"name": str, "preamble": str|null, "parse_ok": true, "raw_markdown": null, '
    '"questions": [{"number": int, "category": str|null, "text": str, "answer": null, '
    '"multi_select": bool, "options": [{"letter": "A".."F"|"X", "text": str, '
    '"is_other": bool, "recommended": bool}]}]}. '
    "multi_select 규칙: 여러 개를 골라도 자연스러운 질문(대상 고객군, 페인포인트 유형 등)은 "
    "true, 배타적 선택(Path/모드 선택 등)은 false(기본). "
    "multi_select 질문의 답변은 'A,C'처럼 콤마로 조인되어 돌아온다. "
    "일반 보기(single-select) 답변은 'B' 또는 'B: 부연설명' 형태로 돌아온다 — "
    "': ' 뒤 부연은 사용자가 그 보기를 고르며 덧붙인 요청/조건이므로 반드시 읽고 반영한다."
)
```

- [ ] **Step 4: 통과 확인 + 백엔드 전체**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_tools.py -q && .venv/bin/python -m pytest -q`
Expected: agent-tools 테스트 전부 PASS, 전체 스위트 그린 (179 + 1 = 180).

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/agent/tools.py backend/tests/test_agent_tools.py
git commit -m "feat(backend): document 'letter: note' answer form in QUESTIONS_SCHEMA_HINT

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 값 계약(`"B: <설명>"`/`"B"`/Other/multi 제외) → Task 1 헬퍼·onChange·테스트. ✓
- 파싱 규약(첫 `": "` 앞 토큰이 letter일 때만 분해, `"Broker: ..."`는 Other) → Task 1 `splitLetterNote` + 복원 테스트 2건. ✓
- UI(선택 보기 아래 펼침, 보기 변경 시 초기화, 복원 렌더) → Task 1 Step 3(c) + 테스트. ✓
- 회귀 가드(otherActive 명시 state 시드 갱신, label 비중첩) → Task 1 Step 3(b)·(c) + 테스트 2건. ✓
- 에이전트 힌트 한 줄 → Task 2. ✓
- 백엔드/데이터 흐름 무변경 확인 → Task 2 Step 4의 전체 스위트 그린. ✓

**Placeholder scan:** 없음 — 모든 스텝에 실제 코드/커맨드 포함.

**Type consistency:** `splitLetterNote(value, letters) → {letter, note} | null` (Task 1 내부 전용, export 없음). `aria-label`은 테스트와 구현 모두 `보기 ${letter} 부연 설명`. `QUESTIONS_SCHEMA_HINT`는 기존 import 경로(`pathfinder.agent.tools`) 그대로.
