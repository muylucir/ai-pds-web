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

  // Regression: 복수선택 질문이 단일선택과 **화면상 구별되지 않았다.** 실제
  // input은 sr-only라 checkbox/radio 글리프가 보이지 않고, 카드 텍스트는 두
  // 모드가 바이트 단위로 동일했다 — 두 번째 보기를 눌러 보기 전까지 여러 개를
  // 고를 수 있다는 사실을 알 방법이 없었다. 배지(문구)와 인디케이터(모양)로
  // 두 단서를 넣었고, 여기서 그 둘이 모드에 따라 갈리는 것을 단정한다.
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
    // sr-only가 아닌, 눈에 보이는 표시를 확인한다 — 이게 없으면 배지 문구만
    // 남고 보기 하나하나가 어떤 컨트롤인지는 여전히 안 보인다.
    const { container: multi } = render(<QuestionCard question={MULTI_Q} value="A" onChange={() => {}} />);
    const multiMarks = [...multi.querySelectorAll('span[aria-hidden="true"]')]
      .filter((el) => el.className.includes("border-2"));
    expect(multiMarks).toHaveLength(3);
    expect(multiMarks.every((el) => el.className.includes("rounded-md"))).toBe(true);
    // 고른 보기만 채워진다.
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
    // multi 모드에서도 동일 구조 보장.
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
  // 회귀: sr-only(absolute) 인풋의 부모 label이 static이면 인풋 좌표가 문서
  // 루트 기준이 되어, 긴 질문지에서 <html>에 유령 오버플로를 만든다. 라벨
  // 클릭(=input.focus())마다 브라우저가 문서를 그 좌표로 스크롤해 헤더가
  // 말려 올라갔다(ui-bug.png). 모든 sr-only 인풋의 offsetParent가 자기
  // label(=relative) 안에 갇혀 있어야 한다.
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
    // 실측(Playwright): 라벨 relative만으로는 html 오버플로 1886→1547px,
    // fieldset relative까지 적용해야 0. legend.sr-only(absolute)가 남은
    // 기여자였다.
    render(
      <Harness question={{ ...q1, multi_select: false }} initial="" spy={vi.fn()} />,
    );
    const fieldset = document.querySelector("fieldset");
    expect(fieldset).not.toBeNull();
    expect(fieldset!.className).toContain("relative");
  });
});

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

  it("multi-select에서 'B: ...' 모양의 Other 자유텍스트가 보기로 오인되지 않는다", () => {
    // multi에는 부연(letter: note) 규약이 없다 — 자기 질문의 letter로 시작하는
    // 자유텍스트("B: 회의 후 결정")도 통째로 Other 텍스트로 복원되어야 한다.
    // (회귀: otherActive 시드가 !multi 게이트 없이 splitLetterNote를 호출해
    // 이 값을 letter+note로 오인 → Other 텍스트박스가 비고, Other 클릭 시
    // 저장값이 ""로 유실됐다.)
    const MULTI_WITH_OTHER = { ...MULTI_Q, options: [...MULTI_Q.options, { letter: "X", text: "Other", is_other: true, recommended: false }] };
    render(<QuestionCard question={MULTI_WITH_OTHER} value="B: 회의 후 결정하겠습니다" onChange={vi.fn()} />);
    expect(screen.getByLabelText(/기타 답변 직접 입력/)).toHaveValue("B: 회의 후 결정하겠습니다");
    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes.filter((c) => c.value === "B").every((c) => !c.checked)).toBe(true);
  });
});

describe("중복 Other 방어 (regression)", () => {
  // 백엔드가 ask_questions 경계에서 정규화하지만, 이 컴포넌트는 그 이전
  // 세션에 저장된 interrupt를 새로고침으로 복원할 때도 쓰인다. is_other가 둘
  // 이상이면 두 라디오가 같은 otherActive 상태를 공유해 선택이 서로를 덮어쓰고,
  // 실질 보기의 텍스트가 "Other — 직접 입력"으로 덮여 사라진다(question.png).
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

// ---- 문항 앞의 설명 산문(context) ----
// 질문 파일에서 온 라운드에만 있다. AskUserQuestion 페이로드에는 이 필드가 없어서
// 사용자는 "왜 이걸 묻는지"를 못 보고 답했다 — 2026-08-17 실측한 확인 게이트 질문은
// "**위에 정리한** 페인 포인트 5건이 정확합니까?"인데 그 "위에 정리한" 표가 파서에서
// 사라져 답할 수 없는 질문으로 화면에 떴다.

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
    // 마크다운으로 렌더 — 줄바꿈이 살아 있어야 표가 성립한다.
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
});
