import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ClarificationBanner } from "./ClarificationBanner";
import { clarificationQuestions } from "@/test/fixtures/clarificationQuestions";

describe("ClarificationBanner", () => {
  it("shows the contradiction heading and links to the clarification file", () => {
    render(
      <ClarificationBanner
        projectId="pilot1"
        path="aiplc-docs/discovery/envision/prfaq-clarification-questions.md"
        preamble={clarificationQuestions.preamble}
      />,
    );
    expect(screen.getByText(/모순이 감지되어 게이트가 보류/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /확인 질문 답변하기/ });
    expect(link.getAttribute("href")).toContain("prfaq-clarification-questions.md");
  });
});
