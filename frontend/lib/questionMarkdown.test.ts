import { describe, it, expect } from "vitest";
import { revealAnswerTags, breakOptionLines, breakAnswerLines, prepareQuestionMarkdown } from "./questionMarkdown";

// 2026-08-16: the backend planted `[Answer]: A`, but not a single answer showed on screen.
// `[Answer]: A` is a CommonMark **link reference definition**, so the renderer produced no output
// for that line, and on top of that it turned the document's other `[Answer]`s into links.

describe("revealAnswerTags", () => {
  it("기록된 답변이 보이는 형태가 된다", () => {
    // This is the reason this function exists.
    expect(revealAnswerTags("[Answer]: A")).toBe("**\\[Answer\\]:** A");
  });

  it("대괄호를 escape한다 — 이게 없으면 여전히 링크 정의다", () => {
    // Without the escape it becomes `**[Answer]:** A`, and the `[Answer]:` inside it can still
    // be read as a definition. This test guards against that regression.
    const out = revealAnswerTags("[Answer]: B");
    expect(out).toContain("\\[Answer\\]");
    expect(out).not.toMatch(/(^|[^\\])\[Answer\]/);
  });

  it("빈 칸도 그대로 보여준다", () => {
    // The fact that a question has not been answered yet is information. Hiding it makes "answered but
    // not visible" indistinguishable from "not asked yet".
    expect(revealAnswerTags("[Answer]:")).toBe("**\\[Answer\\]:**");
    expect(revealAnswerTags("[Answer]: ")).toBe("**\\[Answer\\]:**");
  });

  it("복수 선택과 Other 부연을 그대로 옮긴다", () => {
    expect(revealAnswerTags("[Answer]: A,C")).toBe("**\\[Answer\\]:** A,C");
    expect(revealAnswerTags("[Answer]: X: 사내 감사팀이 먼저 본다")).toBe(
      "**\\[Answer\\]:** X: 사내 감사팀이 먼저 본다",
    );
  });

  it("문항이 여러 개인 문서의 모든 줄을 바꾼다", () => {
    const md = ["## Question 1", "질문?", "", "[Answer]: A", "", "## Question 2", "질문?", "", "[Answer]: B"].join("\n");
    const out = revealAnswerTags(md);
    expect(out).toContain("**\\[Answer\\]:** A");
    expect(out).toContain("**\\[Answer\\]:** B");
  });

  it("문장 **중간**의 `[Answer]:`는 건드리지 않는다", () => {
    // This is the guidance text inside an option: "X) Other (please describe after [Answer]: tag
    // below)". Making it bold would make the guidance look like an answer. It is not at the start of
    // a line, so it is not a link definition either — without a definition, this text renders as is.
    const line = "X) Other (please describe after [Answer]: tag below)";
    expect(revealAnswerTags(line)).toBe(line);
  });

  it("질문 파일의 나머지 본문은 그대로 통과한다", () => {
    // This is why this approach was chosen over rendering with a parser — tables, metadata, and following sections must survive.
    const md = "| # | 필수 영역 |\n|---|---|\n| 1 | 목표 고객 |\n\n[Answer]: A";
    const out = revealAnswerTags(md);
    expect(out).toContain("| 1 | 목표 고객 |");
    expect(out).toContain("**\\[Answer\\]:** A");
  });

  it("`[Answer]:`가 없는 문서는 바뀌지 않는다", () => {
    const md = "# 제목\n\n본문입니다.\n";
    expect(revealAnswerTags(md)).toBe(md);
  });
});


// Options ran together on one line and were unreadable. In CommonMark, lines continuing without a blank line
// are one paragraph, line breaks render as spaces, and `A)` is not a list marker (only digits are recognized).
describe("breakOptionLines", () => {
  it("보기 줄 끝에 하드 브레이크를 붙인다", () => {
    expect(breakOptionLines("A) 연 1~2건")).toBe("A) 연 1~2건  ");
  });

  it("A~F와 X를 모두 본다 — 백엔드 파서와 같은 집합", () => {
    for (const l of ["A", "B", "C", "D", "E", "F", "X"]) {
      expect(breakOptionLines(`${l}) 보기`)).toBe(`${l}) 보기  `);
    }
  });

  it("연속한 보기가 각각 줄바꿈된다", () => {
    const out = breakOptionLines("A) 하나\nB) 둘\nX) Other (please describe after [Answer]: tag below)");
    expect(out).toBe("A) 하나  \nB) 둘  \nX) Other (please describe after [Answer]: tag below)  ");
  });

  it("이미 공백이 있어도 정확히 2개로 정규화한다", () => {
    // Appending duplicates only adds spaces, and 3 or more is still a hard break, so it is harmless, but
    // if the output varied with the input, snapshot comparisons would wobble.
    expect(breakOptionLines("A) 보기    ")).toBe("A) 보기  ");
  });

  it("보기가 아닌 줄은 건드리지 않는다", () => {
    // The question body, tables, and headings must remain as is — this is why we do not render with a parser.
    const md = "## Question 1\n통관 단계에서 …?\n\n| # | 필수 영역 |\n|---|---|";
    expect(breakOptionLines(md)).toBe(md);
    // A-J, the same as the backend's `_OPTION` — it accepts up to the `G)` the model actually uses, and
    // does not accept other letters since they may be parentheses in prose.
    expect(breakOptionLines("K) 아님")).toBe("K) 아님");
  });

  it("letter 뒤에 공백이 없으면 보기가 아니다", () => {
    // The backend's `_OPTION` requires `\s+`, so the same boundary is kept.
    expect(breakOptionLines("A)붙어있음")).toBe("A)붙어있음");
  });
});

// 2026-10-25: a multi-line elaboration is written as a block (the tag line plus indented
// continuation lines), but those lines formed one paragraph, so the user's line breaks rendered as spaces.
describe("breakAnswerLines", () => {
  it("여러 줄 답의 각 줄에 하드 브레이크를 붙인다", () => {
    const md = "[Answer]: A: 첫 줄\n  둘째 줄\n\n  셋째 문단";
    expect(breakAnswerLines(md)).toBe("[Answer]: A: 첫 줄  \n  둘째 줄  \n\n  셋째 문단  ");
  });

  it("블록이 끝나면 그 뒤 줄은 건드리지 않는다 — 백엔드 파서와 같은 경계", () => {
    for (const stop of ["## Question 2", "---", "```", "[Answer]:"]) {
      const out = breakAnswerLines(`[Answer]: A\n\n${stop}\n본문`);
      expect(out).toBe(`[Answer]: A  \n\n${stop}\n본문`);
    }
  });

  it("빈 태그는 블록을 열지 않는다", () => {
    const md = "[Answer]:\n에이전트 설명";
    expect(breakAnswerLines(md)).toBe(md);
  });

  it("CRLF 줄끝을 유지한다", () => {
    expect(breakAnswerLines("[Answer]: A\r\n  둘째\r\n")).toBe("[Answer]: A  \r\n  둘째  \r\n");
  });

  it("문서에 그려도 첫 줄의 하드 브레이크가 남는다", () => {
    const out = prepareQuestionMarkdown("[Answer]: A: 첫 줄\n  둘째 줄");
    expect(out).toBe("**\\[Answer\\]:** A: 첫 줄  \n  둘째 줄  ");
  });
});

describe("prepareQuestionMarkdown", () => {
  it("두 변환을 함께 적용한다", () => {
    const md = "A) 하나\nB) 둘\n\n[Answer]: B";
    const out = prepareQuestionMarkdown(md);
    expect(out).toContain("A) 하나  ");
    expect(out).toContain("B) 둘  ");
    expect(out).toContain("**\\[Answer\\]:** B");
  });
});
