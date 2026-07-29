import { describe, it, expect } from "vitest";
import { answerSummary } from "./answerSummary";
import type { QuestionFile, QuestionOption } from "@/lib/api/types";

function opt(letter: string, text: string, is_other = false): QuestionOption {
  return { letter, text, is_other, recommended: false };
}

function file(questions: QuestionFile["questions"]): QuestionFile {
  return { name: "q.md", preamble: null, questions, parse_ok: true, raw_markdown: null };
}

function q(
  number: number,
  text: string,
  options: QuestionOption[] = [],
  multi_select = false,
) {
  return { number, category: null, text, options, answer: null, multi_select };
}

describe("answerSummary", () => {
  it("pairs each answer with the question it answers", () => {
    const qf = file([
      q(1, "주 사용자는 누구입니까?"),
      q(2, "출시 목표 시점은?"),
    ]);

    expect(answerSummary(qf, { "1": "사내 QA 담당자", "2": "2개월 이내" })).toBe(
      "Q1. 주 사용자는 누구입니까?\n→ 사내 QA 담당자\n\nQ2. 출시 목표 시점은?\n→ 2개월 이내",
    );
  });

  it("expands an option letter to its text so the bubble is readable", () => {
    // The form submits the LETTER ("A"), which alone tells a reader nothing —
    // the point of this summary is that the transcript makes sense without
    // re-opening the question form.
    const qf = file([
      q(1, "어떤 방식이 좋습니까?", [opt("A", "기존 도구 확장"), opt("B", "신규 개발")]),
    ]);

    expect(answerSummary(qf, { "1": "A" })).toBe(
      "Q1. 어떤 방식이 좋습니까?\n→ A. 기존 도구 확장",
    );
  });

  it("keeps the note attached to a 'letter: note' answer", () => {
    // QuestionCard's value contract: a chosen option carrying a free-text
    // addendum arrives as the single string "A: 부연". Both halves matter.
    const qf = file([
      q(1, "어떤 방식이 좋습니까?", [opt("A", "기존 도구 확장"), opt("B", "신규 개발")]),
    ]);

    expect(answerSummary(qf, { "1": "A: 단 인증만 새로" })).toBe(
      "Q1. 어떤 방식이 좋습니까?\n→ A. 기존 도구 확장 — 단 인증만 새로",
    );
  });

  it("expands every letter of a comma-joined multi-select answer", () => {
    const qf = file([
      q(
        1,
        "필요한 기능을 고르세요",
        [opt("A", "자동 생성"), opt("B", "수동 편집"), opt("C", "이력 관리")],
        true,
      ),
    ]);

    expect(answerSummary(qf, { "1": "A,C" })).toBe(
      "Q1. 필요한 기능을 고르세요\n→ A. 자동 생성, C. 이력 관리",
    );
  });

  it("keeps free text whose first token merely looks like a letter", () => {
    // QuestionCard deliberately does NOT infer Other-mode from the value, for
    // exactly this case. A summary that split on ": " here would mangle it.
    const qf = file([q(1, "다른 의견이 있으면 적어주세요", [opt("A", "없음")])]);

    expect(answerSummary(qf, { "1": "Broker: 큐를 따로 두고 싶다" })).toBe(
      "Q1. 다른 의견이 있으면 적어주세요\n→ Broker: 큐를 따로 두고 싶다",
    );
  });

  it("passes an Other option's own text through without expanding it", () => {
    // An is_other option's letter is not a label the reader needs; the typed
    // text IS the answer.
    const qf = file([
      q(1, "어떤 방식이 좋습니까?", [opt("A", "기존 도구 확장"), opt("X", "", true)]),
    ]);

    expect(answerSummary(qf, { "1": "직접 만든 스크립트로" })).toBe(
      "Q1. 어떤 방식이 좋습니까?\n→ 직접 만든 스크립트로",
    );
  });

  it("skips questions the user left blank", () => {
    const qf = file([q(1, "첫 질문"), q(2, "두 번째 질문")]);

    expect(answerSummary(qf, { "1": "답", "2": "" })).toBe("Q1. 첫 질문\n→ 답");
  });

  it("falls back to a bare line when an answer has no matching question", () => {
    // Defensive: the payload could carry a key the question list does not
    // describe (a stale form, a server-side renumber). Losing the answer would
    // be worse than showing it without its question.
    const qf = file([q(1, "아는 질문")]);

    expect(answerSummary(qf, { "1": "답", "9": "고아 답변" })).toBe(
      "Q1. 아는 질문\n→ 답\n\nQ9.\n→ 고아 답변",
    );
  });

  it("returns a marker rather than an empty bubble when nothing was answered", () => {
    const qf = file([q(1, "질문")]);

    // An empty string would render as a blank user bubble — the very thing
    // this helper exists to prevent.
    expect(answerSummary(qf, {})).toBe("답변 제출");
    expect(answerSummary(qf, { "1": "   " })).toBe("답변 제출");
  });
});
