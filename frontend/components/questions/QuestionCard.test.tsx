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
