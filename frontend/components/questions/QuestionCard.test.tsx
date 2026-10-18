import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionCard } from "./QuestionCard";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import type { Question } from "@/lib/api/types";

const MULTI_Q: Question = {
  number: 1,
  category: null,
  text: "페인포인트 유형은?",
  answer: null,
  multi_select: true,
  options: [
    { letter: "A", text: "속도", is_other: false, recommended: false },
    { letter: "B", text: "비용", is_other: false, recommended: false },
    { letter: "C", text: "품질", is_other: false, recommended: false },
  ],
};

const q1 = strategyQuestions.questions[0];

// Stateful harness: QuestionCard is a controlled component, so a bare vi.fn()
// onChange would leave `value` frozen and userEvent.type would report each
// keystroke against a non-advancing value (last call = a single char). This
// wrapper holds real state so `value` advances, letting us assert the final
// accumulated string while still spying on every onChange call.
function Harness({ question, initial, spy }: { question: Question; initial: string; spy: (v: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <QuestionCard
      question={question}
      value={value}
      onChange={(v) => {
        spy(v);
        setValue(v);
      }}
    />
  );
}

describe("QuestionCard", () => {
  it("renders every option with letters and the ★ recommended pill", () => {
    render(<QuestionCard question={q1} value="A" onChange={vi.fn()} />);
    expect(screen.getByText(/Niche Specialist/)).toBeInTheDocument();
    expect(screen.getByText("★ AI 추천")).toBeInTheDocument(); // Q1 option A recommended
    expect(screen.getByText("X")).toBeInTheDocument(); // Other option chip
  });

  it("selecting an option reports its letter", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={q1} initial="A" spy={spy} />);
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    expect(spy).toHaveBeenCalledWith("B");
  });

  it("typing in the Other textarea reports the free text", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={q1} initial="" spy={spy} />);
    await user.type(screen.getByLabelText(/기타 답변 직접 입력/), "커스텀");
    // With the stateful harness, value accumulates, so the last onChange carries
    // the full string.
    expect(spy).toHaveBeenLastCalledWith("커스텀");
  });

  it("renders checkboxes for multi_select and joins letters with comma", () => {
    let value = "";
    const onChange = (v: string) => { value = v; };
    const { rerender } = render(<QuestionCard question={MULTI_Q} value={value} onChange={onChange} />);
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    fireEvent.click(screen.getByRole("checkbox", { name: /속도/ }));
    rerender(<QuestionCard question={MULTI_Q} value={value} onChange={onChange} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /품질/ }));
    expect(value).toBe("A,C");
  });

  it("unchecking removes the letter", () => {
    let value = "A,C";
    render(<QuestionCard question={MULTI_Q} value={value} onChange={(v) => { value = v; }} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /속도/ }));
    expect(value).toBe("C");
  });

  // Regression: multi-select questions were **visually indistinguishable** from single-select. The actual
  // input is sr-only, so no checkbox/radio glyph is visible, and the card text of the two
  // modes was byte-for-byte identical — there was no way to know several could be chosen
  // until you clicked a second option. Two cues were added, a badge (wording) and an indicator (shape),
  // and this asserts that both differ by mode.
  it("복수선택 질문에 '여러 개 선택 가능' 배지를 단다", () => {
    render(<QuestionCard question={MULTI_Q} value="" onChange={() => {}} />);
    expect(screen.getByText("여러 개 선택 가능")).toBeInTheDocument();
    expect(screen.queryByText("하나만 선택")).toBeNull();
  });

  it("단일선택 질문에는 '하나만 선택' 배지를 단다", () => {
    render(<QuestionCard question={{ ...MULTI_Q, multi_select: false }} value="" onChange={() => {}} />);
    expect(screen.getByText("하나만 선택")).toBeInTheDocument();
    expect(screen.queryByText("여러 개 선택 가능")).toBeNull();
  });

  it("보이는 인디케이터가 모드에 따라 네모/동그라미로 갈린다", () => {
    // Check the visible mark, not the sr-only one — without it only the badge wording
    // remains, and which control each option is still cannot be seen.
    const { container: multi } = render(<QuestionCard question={MULTI_Q} value="A" onChange={() => {}} />);
    const multiMarks = [...multi.querySelectorAll('span[aria-hidden="true"]')]
      .filter((el) => el.className.includes("border-2"));
    expect(multiMarks).toHaveLength(3);
    expect(multiMarks.every((el) => el.className.includes("rounded-md"))).toBe(true);
    // Only the chosen option is filled.
    expect(multiMarks.filter((el) => el.className.includes("bg-violet-600"))).toHaveLength(1);

    const { container: single } = render(
      <QuestionCard question={{ ...MULTI_Q, multi_select: false }} value="A" onChange={() => {}} />,
    );
    const singleMarks = [...single.querySelectorAll('span[aria-hidden="true"]')]
      .filter((el) => el.className.includes("border-2"));
    expect(singleMarks).toHaveLength(3);
    expect(singleMarks.every((el) => el.className.includes("rounded-full"))).toBe(true);
  });

  it("single-select questions still render radios", () => {
    render(<QuestionCard question={{ ...MULTI_Q, multi_select: false }} value="" onChange={() => {}} />);
    expect(screen.getAllByRole("radio").length).toBeGreaterThan(0);
  });

  // Regression: the Other free-text <textarea> must NOT be nested inside the
  // Other option's radio/checkbox <label>. When it was, clicking the Other
  // area focused the (sr-only) radio instead of the textarea, and the first
  // keystroke (e.g. Shift+A) was swallowed by the label→control activation and
  // leaked into the radio group — the user's "typing selects option A / first
  // char lost" bug. Verified in real Chromium (label-nested textarea drops the
  // first character); the structural guarantee that prevents it is: the
  // textarea has no ancestor <label>.
  it("Other 텍스트 입력창은 라디오 label 안에 중첩되지 않는다 (첫 글자 유실/라디오 오선택 회귀)", () => {
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={vi.fn()} />);
    const textarea = screen.getByLabelText(/기타 답변 직접 입력/);
    expect(textarea.closest("label")).toBeNull();
  });

  it("Other 옵션의 라디오/체크박스도 textarea를 label 자식으로 두지 않는다", () => {
    // Guarantee the same structure in multi mode too.
    render(<QuestionCard question={{ ...MULTI_Q, multi_select: true, options: [...MULTI_Q.options, { letter: "X", text: "Other", is_other: true, recommended: false }] }} value="" onChange={vi.fn()} />);
    const textarea = screen.getByLabelText(/기타 답변 직접 입력/);
    expect(textarea.closest("label")).toBeNull();
  });

  // Regression (root cause): the Other free-text value shares its string space
  // with option letters (A/B/X…). When the user typed a single letter that
  // happened to match an option — e.g. the first char "A" of "Apple" — the
  // component mistook value==="A" for "option A is selected", flipped out of
  // Other mode, and blanked the textarea (the first char was lost, and option
  // A rendered as checked). Free-text mode must be tracked explicitly, not
  // inferred by comparing the value against option letters.
  it("Other 자유입력이 옵션 letter와 겹치는 한 글자여도 보기로 오인되지 않는다", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={spy} />);
    const textarea = screen.getByLabelText(/기타 답변 직접 입력/);
    await user.click(textarea);
    await user.type(textarea, "A");
    // The single "A" must land in the textarea as free text, NOT select option A.
    expect(spy).toHaveBeenLastCalledWith("A");
    expect(textarea).toHaveValue("A");
    // No radio should be checked (A is free text, not the option).
    const optionA = screen.getAllByRole("radio")[0] as HTMLInputElement;
    expect(optionA.checked).toBe(false);
  });

  it("한 글자 입력 뒤 이어 타이핑해도 첫 글자가 유실되지 않는다", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={spy} />);
    const textarea = screen.getByLabelText(/기타 답변 직접 입력/);
    await user.click(textarea);
    await user.type(textarea, "Apple");
    expect(spy).toHaveBeenLastCalledWith("Apple");
    expect(textarea).toHaveValue("Apple");
  });
});

describe("QuestionCard — sr-only 인풋의 포지셔닝 컨텍스트 (스크롤 말림 회귀)", () => {
  // Regression: if the parent label of an sr-only (absolute) input is static, the input's coordinates are
  // relative to the document root, creating phantom overflow on <html> in long questionnaires. On every label
  // click (= input.focus()) the browser scrolled the document to those coordinates and the header
  // rolled up (ui-bug.png). Every sr-only input's offsetParent must be confined within its own
  // label (= relative).
  it("모든 옵션 인풋의 label이 relative 포지셔닝 컨텍스트를 만든다", () => {
    render(
      <Harness question={{ ...q1, multi_select: false }} initial="" spy={vi.fn()} />,
    );
    const inputs = document.querySelectorAll("input.sr-only");
    expect(inputs.length).toBeGreaterThan(0);
    for (const input of inputs) {
      const label = input.closest("label");
      expect(label).not.toBeNull();
      expect(label!.className).toContain("relative");
    }
  });

  it("fieldset도 relative — sr-only legend를 카드 안에 가둔다", () => {
    // Observed (Playwright): with only the label relative, html overflow went 1886→1547px;
    // it reached 0 only with fieldset relative as well. legend.sr-only (absolute) was the remaining
    // contributor.
    render(
      <Harness question={{ ...q1, multi_select: false }} initial="" spy={vi.fn()} />,
    );
    const fieldset = document.querySelector("fieldset");
    expect(fieldset).not.toBeNull();
    expect(fieldset!.className).toContain("relative");
  });
});

describe("QuestionCard — 보기 부연 설명 (letter + note)", () => {
  // Spec (2026-07-21-option-annotation-design.md): choosing a regular option expands a
  // '부연 설명 (선택)' (additional note, optional) input under it, and typing submits a single
  // string "B: <note>". The value contract that reproduces Kiro/Claude Code's "[Answer]: letter + note" experience
  // in the AI-PDS form, without editing files.

  it("보기를 선택하면 부연 설명 입력란이 그 보기 아래 펼쳐진다", async () => {
    const user = userEvent.setup();
    render(<Harness question={{ ...q1, multi_select: false }} initial="" spy={vi.fn()} />);
    // No note input before selecting
    expect(screen.queryByLabelText(/보기 B 부연 설명/)).toBeNull();
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    expect(screen.getByLabelText(/보기 B 부연 설명/)).toBeInTheDocument();
    // No input on the other (unselected) options
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
    expect(spy).toHaveBeenLastCalledWith("A");           // letter only, no note
    expect(screen.getByLabelText(/보기 A 부연 설명/)).toHaveValue("");
  });

  it("저장된 'B: 설명' 값 복원 시 보기 B 선택 + 부연이 채워진다", () => {
    render(<Harness question={{ ...q1, multi_select: false }} initial="B: 헤드라인 수정" spy={vi.fn()} />);
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    const radioB = radios.find((r) => r.value === "B")!;
    expect(radioB.checked).toBe(true);
    expect(screen.getByLabelText(/보기 B 부연 설명/)).toHaveValue("헤드라인 수정");
    // The Other text box must be empty (must not be mistaken for free text)
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

  it("multi-select에서 'B: ...' 모양의 Other 자유텍스트가 보기로 오인되지 않는다", () => {
    // multi has no note (letter: note) convention — free text starting with the question's own letter
    // ("B: 회의 후 결정") must also be restored whole as Other text.
    // (Regression: the otherActive seed called splitLetterNote without the !multi gate,
    // mistaking this value for letter+note → the Other text box went empty, and clicking Other
    // lost the saved value to "".)
    const MULTI_WITH_OTHER = { ...MULTI_Q, options: [...MULTI_Q.options, { letter: "X", text: "Other", is_other: true, recommended: false }] };
    render(<QuestionCard question={MULTI_WITH_OTHER} value="B: 회의 후 결정하겠습니다" onChange={vi.fn()} />);
    expect(screen.getByLabelText(/기타 답변 직접 입력/)).toHaveValue("B: 회의 후 결정하겠습니다");
    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes.filter((c) => c.value === "B").every((c) => !c.checked)).toBe(true);
  });
});

describe("중복 Other 방어 (regression)", () => {
  // The backend normalizes at the ask_questions boundary, but this component is also used when
  // restoring, on refresh, an interrupt saved in a session from before that. With two or more is_other,
  // the two radios share the same otherActive state, so selections overwrite each other,
  // and the real option's text is covered by "Other — 직접 입력" and disappears (question.png).
  const dupOther = {
    number: 1, category: null, text: "다음 단계로 무엇을 할까요?", answer: null,
    multi_select: false,
    options: [
      { letter: "B", text: "이 사양서 그대로 핸드오프", is_other: true, recommended: false },
      { letter: "X", text: "Other — 직접 입력", is_other: true, recommended: false },
    ],
  };

  it("renders only one Other input and keeps the real option's text", () => {
    render(<QuestionCard question={dupOther} value="" onChange={vi.fn()} />);
    expect(screen.getAllByText(/Other — 직접 입력/)).toHaveLength(1);
    expect(screen.getByText("이 사양서 그대로 핸드오프")).toBeInTheDocument();
  });

  it("lets the demoted option be selected as a normal choice", () => {
    const onChange = vi.fn();
    render(<QuestionCard question={dupOther} value="" onChange={onChange} />);
    fireEvent.click(screen.getByText("이 사양서 그대로 핸드오프"));
    expect(onChange).toHaveBeenCalledWith("B");
  });
});

// ---- Explanatory prose before a question (context) ----
// Present only in rounds that come from a question file. The AskUserQuestion payload has no such field, so
// users answered without seeing "why this is being asked" — the confirmation-gate question observed on 2026-08-17 was
// "**위에 정리한** 페인 포인트 5건이 정확합니까?" (are the 5 pain points **summarized above** correct?), but the "위에 정리한" table
// vanished in the parser and it showed up on screen as an unanswerable question.

const CONTEXT_Q: Question = {
  number: 1,
  category: "확인 대상 요약",
  text: "위에 정리한 내용이 정확합니까?",
  context: "| # | 페인 포인트 |\n|---|---|\n| 1 | 반복 삭감 |\n| 2 | 사일로 |",
  answer: null,
  options: [{ letter: "A", text: "정확하다", is_other: false, recommended: false }],
};

describe("QuestionCard — context", () => {
  it("표가 표로 렌더된다 (평문이면 답할 수 없는 질문이 된다)", () => {
    render(<QuestionCard question={CONTEXT_Q} value="" onChange={() => {}} />);
    // Rendered as markdown — line breaks must survive for the table to hold together.
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("반복 삭감")).toBeInTheDocument();
    expect(screen.getByText("사일로")).toBeInTheDocument();
  });

  it("context가 없으면 아무것도 그리지 않는다", () => {
    render(<QuestionCard question={MULTI_Q} value="" onChange={() => {}} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("공백뿐인 context는 빈 블록을 남기지 않는다", () => {
    render(
      <QuestionCard question={{ ...CONTEXT_Q, context: "   \n\n  " }} value=""
                    onChange={() => {}} />,
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  // Observed 2026-08-18 (ws-k): the background went below the title, so the card
  // read as "question → 1,126 characters of background + table → input", and the user misdiagnosed it
  // as a parse error. In the file, this prose sits **before** the `## Question N` heading.
  it("배경이 질문 문장보다 위에 온다 — 파일에 쓰인 순서다", () => {
    const { container } = render(
      <QuestionCard question={CONTEXT_Q} value="" onChange={() => {}} />,
    );
    const table = screen.getByRole("table");
    const heading = screen.getByRole("heading", { level: 2 });
    // DOCUMENT_POSITION_FOLLOWING: the heading is **after** the table.
    expect(
      table.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(container).toBeTruthy();
  });
});

// ---- Inline markdown in options and question sentences ----
// Observed 2026-08-18: raw `**` showed up as is in options. Options in question files really are
// written like this (prfaq-clarifying-questions.md):
//
//     A) **「조정 브리프」** — 조정 건 하나를 열면 근거·반복 이력·과거 사례가 …
//
// 질문 문장도 같다: `제품명을 무엇으로 할까요? *(보도자료의 Heading, …)*`.
// The shared `Markdown` was only applied to the preamble and documents; the card drew plain text.
//
// **It must be inline-only.** Options are a flex layout inside `<label>`, so block elements (`<p>`) and
// prose margins coming in break the alignment. And headings, lists or tables in an option label are
// invalid input in the first place, so it is better not to render them.

const MD_Q: Question = {
  number: 1,
  category: null,
  text: "제품명을 무엇으로 할까요? *(보도자료의 Heading)*",
  answer: null,
  options: [
    { letter: "A", text: "**「조정 브리프」** — 한 장으로 묶여 나온다는 뜻",
      is_other: false, recommended: false },
    { letter: "B", text: "`코드체` 보기", is_other: false, recommended: false },
  ],
};

describe("QuestionCard — 인라인 마크다운", () => {
  it("보기의 굵은체가 원문(**)이 아니라 굵게 렌더된다", () => {
    render(<QuestionCard question={MD_Q} value="" onChange={() => {}} />);
    // Raw `**` must not remain on screen.
    expect(screen.queryByText(/\*\*/)).toBeNull();
    const strong = screen.getByText("「조정 브리프」");
    expect(strong.tagName).toBe("STRONG");
  });

  it("질문 문장의 기울임체도 렌더된다", () => {
    render(<QuestionCard question={MD_Q} value="" onChange={() => {}} />);
    const em = screen.getByText("(보도자료의 Heading)");
    expect(em.tagName).toBe("EM");
  });

  it("코드체 보기도 렌더된다", () => {
    render(<QuestionCard question={MD_Q} value="" onChange={() => {}} />);
    expect(screen.getByText("코드체").tagName).toBe("CODE");
  });

  it("문단을 감싸지 않는다 — 카드의 <p> 안에 <p>를 중첩하면 잘못된 HTML이다", () => {
    const { container } = render(
      <QuestionCard question={MD_Q} value="" onChange={() => {}} />);
    // The card itself uses `<p className="font-medium">` (that is fine). If the renderer makes
    // another <p> inside it, the browser splits the paragraph and the layout breaks.
    expect(container.querySelectorAll("p p").length).toBe(0);
  });

  it("보기에 블록 마크다운이 와도 카드를 부수지 않는다 (태그만 벗기고 글자는 남긴다)", () => {
    // Headings or lists in an option label are invalid input. Rendering them breaks the card,
    // and dropping them loses the text — unwrapDisallowed keeps only the content.
    const blocky: Question = {
      ...MD_Q,
      options: [{ letter: "A", text: "## 헤딩\n\n- 목록 항목",
                  is_other: false, recommended: false }],
    };
    const { container } = render(
      <QuestionCard question={blocky} value="" onChange={() => {}} />);
    // The card's own question heading (<h2>) is fine, so narrow the scope to inside the options.
    expect(container.querySelectorAll(
      "label h1, label h2, label h3, label ul, label ol, label li").length).toBe(0);
    expect(screen.getByText(/헤딩/)).toBeInTheDocument();
    expect(screen.getByText(/목록 항목/)).toBeInTheDocument();
  });

  it("마크다운이 없는 보기는 그대로 나온다", () => {
    render(<QuestionCard question={MULTI_Q} value="" onChange={() => {}} />);
    expect(screen.getByText("속도")).toBeInTheDocument();
  });
});

// ---- A question with no options = free text (observed 2026-08-18: ws-k) ----
// Mode A (free description) of Envision Step 0.2 normally asks without options,
// and the parser read that file correctly (5 questions, question text fine, options=0). But this
// component only iterates over the options array, so the card body was empty and **there was no way
// to answer at all.** The badge said "하나만 선택" (choose one) when there were 0 things to choose.

const FREEFORM_Q: Question = {
  number: 1,
  category: null,
  text: "어떤 산업 또는 비즈니스 도메인을 위한 제품입니까?",
  answer: null,
  multi_select: false,
  options: [],
};

describe("보기가 없는 문항", () => {
  it("입력칸을 그린다 — 이것이 없으면 답을 낼 방법이 없다", () => {
    render(<QuestionCard question={FREEFORM_Q} value="" onChange={vi.fn()} />);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("'하나만 선택' 배지를 달지 않는다 — 고를 것이 0개다", () => {
    render(<QuestionCard question={FREEFORM_Q} value="" onChange={vi.fn()} />);
    expect(screen.queryByText("하나만 선택")).not.toBeInTheDocument();
    expect(screen.getByText("자유 답변")).toBeInTheDocument();
  });

  it("입력한 문장을 그대로 올린다 — letter 규약을 거치지 않는다", async () => {
    const spy = vi.fn();
    render(<Harness question={FREEFORM_Q} initial="" spy={spy} />);
    await userEvent.type(screen.getByRole("textbox"), "B2B SaaS 물류");
    expect(spy).toHaveBeenLastCalledWith("B2B SaaS 물류");
  });

  it("복원된 답변이 첫 렌더에 보인다", () => {
    // With options, free text goes through the `otherActive` state, but without options
    // that state cannot be relied on — it must bind to value directly.
    render(<QuestionCard question={FREEFORM_Q} value="이미 답한 내용" onChange={vi.fn()} />);
    expect(screen.getByRole("textbox")).toHaveValue("이미 답한 내용");
  });

  it("라디오·체크박스를 만들지 않는다", () => {
    render(<QuestionCard question={FREEFORM_Q} value="" onChange={vi.fn()} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
