// frontend/components/canvas/HistoryQuestionsCard.test.tsx
//
// 스크롤백의 읽기 전용 질문 카드. **라이브 카드와 같은 표현이어야 한다** — 방금 본
// 화면과 스크롤백이 다르면 사용자는 무엇이 진짜인지 알 수 없다.
//
// 2026-08-18: 보기의 마크다운 원문(`**`)이 그대로 뜨는 결함을 라이브 카드
// (QuestionCard)에서 고쳤는데, 같은 문자열을 그리는 읽기 전용 카드 셋이 남아 있었다.
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
    // 이 카드는 접혀 있다 — 펼쳐야 문항이 그려진다.
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/\*\*/)).toBeNull();
    expect(screen.getByText("「조정 브리프」").tagName).toBe("STRONG");
    expect(screen.getByText("(보도자료의 Heading)").tagName).toBe("EM");
  });
});
