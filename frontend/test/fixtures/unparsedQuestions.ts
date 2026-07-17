import type { QuestionFile } from "@/lib/api/types";

// parse_ok=false payload — the backend returns this whenever a question file
// doesn't match the strict format; the wizard must fall back to raw markdown.
export const unparsedQuestions: QuestionFile = {
  name: "freeform-notes.md",
  preamble: null,
  parse_ok: false,
  raw_markdown:
    "# 자유 형식 메모\n\n이 파일은 표준 질문 형식이 아닙니다.\n\n- 항목 1\n- 항목 2\n\n자유롭게 답변을 작성해 주세요.",
  questions: [],
};
