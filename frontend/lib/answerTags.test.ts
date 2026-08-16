import { describe, it, expect } from "vitest";
import { revealAnswerTags } from "./answerTags";

// 2026-08-16: 백엔드가 `[Answer]: A`를 심었는데 화면에 답변이 하나도 안 보였다.
// `[Answer]: A`가 CommonMark **링크 참조 정의**라서 렌더러가 그 줄에 대해 아무
// 출력도 만들지 않고, 게다가 문서의 다른 `[Answer]`를 링크로 바꿔버렸기 때문이다.

describe("revealAnswerTags", () => {
  it("기록된 답변이 보이는 형태가 된다", () => {
    // 이것이 이 함수의 존재 이유다.
    expect(revealAnswerTags("[Answer]: A")).toBe("**\\[Answer\\]:** A");
  });

  it("대괄호를 escape한다 — 이게 없으면 여전히 링크 정의다", () => {
    // escape를 빼면 `**[Answer]:** A`가 되고, 그 안의 `[Answer]:`는 여전히
    // 정의로 읽힐 수 있다. 이 테스트가 그 회귀를 막는다.
    const out = revealAnswerTags("[Answer]: B");
    expect(out).toContain("\\[Answer\\]");
    expect(out).not.toMatch(/(^|[^\\])\[Answer\]/);
  });

  it("빈 칸도 그대로 보여준다", () => {
    // 아직 답하지 않은 문항이라는 사실이 정보다. 숨기면 "답했는데 안 보인다"와
    // "아직 안 물어봤다"를 구별할 수 없다.
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
    // 보기 텍스트의 안내 문구다: "X) Other (please describe after [Answer]: tag
    // below)". 이걸 굵게 바꾸면 안내가 답변처럼 보인다. 줄 맨 앞이 아니므로
    // 링크 정의도 아니다 — 정의가 사라지면 이 텍스트는 그대로 렌더된다.
    const line = "X) Other (please describe after [Answer]: tag below)";
    expect(revealAnswerTags(line)).toBe(line);
  });

  it("질문 파일의 나머지 본문은 그대로 통과한다", () => {
    // 파서로 렌더하지 않고 이 방식을 택한 이유다 — 표·메타·후속 절이 살아야 한다.
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
