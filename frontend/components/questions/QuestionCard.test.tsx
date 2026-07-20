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
