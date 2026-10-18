// frontend/components/canvas/HistoryQuestionsCard.test.tsx
//
// The read-only question card in the scrollback. **It must render the same way as the live
// card** — if the screen just seen and the scrollback differ, the user cannot tell which is real.
//
// 2026-08-18: the defect where option markdown source (`**`) showed up raw was fixed in the live
// card (QuestionCard), but three read-only cards drawing the same strings were left unfixed.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HistoryQuestionsCard } from "./HistoryQuestionsCard";
import type { QuestionFile } from "@/lib/api/types";

const FILE: QuestionFile = {
  name: "prfaq-clarifying-questions.md",
  preamble: null,
  parse_ok: true,
  raw_markdown: null,
  questions: [
    {
      number: 1,
      category: null,
      text: "제품명을 무엇으로 할까요? *(보도자료의 Heading)*",
      answer: "A",
      options: [
        { letter: "A", text: "**「조정 브리프」** — 한 장으로 묶인다",
          is_other: false, recommended: false },
      ],
    },
  ],
};

describe("HistoryQuestionsCard", () => {
  it("보기와 질문의 마크다운을 원문(**)이 아니라 렌더한다", async () => {
    render(<HistoryQuestionsCard name={FILE.name} file={FILE} />);
    // This card is collapsed — the questions are drawn only once it is expanded.
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/\*\*/)).toBeNull();
    expect(screen.getByText("「조정 브리프」").tagName).toBe("STRONG");
    expect(screen.getByText("(보도자료의 Heading)").tagName).toBe("EM");
  });
});
