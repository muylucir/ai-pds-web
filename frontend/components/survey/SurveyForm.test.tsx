import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SurveyForm } from "./SurveyForm";
import type { SurveyQuestion } from "@/lib/api/surveys";

const QUESTIONS: SurveyQuestion[] = [
  { id: "q1", text: "유용했나요?", type: "scale", options: [], required: true },
  { id: "q2", text: "어느 기능?", type: "choice", options: ["요약", "검색"], required: true },
  { id: "q3", text: "개선점", type: "text", options: [], required: false },
];

describe("SurveyForm", () => {
  it("renders all three question types", () => {
    render(<SurveyForm questions={QUESTIONS} onSubmit={vi.fn()} submitting={false} />);
    expect(screen.getByText("유용했나요?")).toBeInTheDocument();
    expect(screen.getByLabelText("요약")).toBeInTheDocument();
    expect(screen.getByText("개선점")).toBeInTheDocument();
  });

  it("blocks submit until required answers are given", async () => {
    const onSubmit = vi.fn();
    render(<SurveyForm questions={QUESTIONS} onSubmit={onSubmit} submitting={false} />);
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/필수/)).toBeInTheDocument();
  });

  it("submits scale as a number and choice as its label", async () => {
    const onSubmit = vi.fn();
    render(<SurveyForm questions={QUESTIONS} onSubmit={onSubmit} submitting={false} />);
    await userEvent.click(screen.getByRole("radio", { name: "4" }));
    await userEvent.click(screen.getByLabelText("요약"));
    await userEvent.type(screen.getByLabelText("개선점"), "속도 개선");
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).toHaveBeenCalledWith({ q1: 4, q2: "요약", q3: "속도 개선" });
  });

  it("omits an untouched optional text answer", async () => {
    const onSubmit = vi.fn();
    render(<SurveyForm questions={QUESTIONS} onSubmit={onSubmit} submitting={false} />);
    await userEvent.click(screen.getByRole("radio", { name: "5" }));
    await userEvent.click(screen.getByLabelText("검색"));
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).toHaveBeenCalledWith({ q1: 5, q2: "검색" });
  });

  it("disables the submit button while submitting", () => {
    render(<SurveyForm questions={QUESTIONS} onSubmit={vi.fn()} submitting />);
    expect(screen.getByRole("button", { name: /제출/ })).toBeDisabled();
  });
});
