import type { QuestionFile } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/discovery/product-strategy/strategy-questions.md
// (13 questions across Positioning / Differentiation / Business Model / Success
// Metrics / Risks). Trimmed option prose for brevity; structure is faithful:
// each question ends with an X) Other option (is_other:true), the pilot's
// recommended default carries recommended:true, and [Answer] values are captured.
const other = (letter: string) => ({
  letter,
  text: "Other (please describe after [Answer]: tag below)",
  is_other: true,
  recommended: false,
});

export const strategyQuestions: QuestionFile = {
  name: "strategy-questions.md",
  preamble:
    "**참고**: Prototype & Validation 단계에서 실사용자 검증이 스킵되어, 아래 추천 기본값은 Envision 단계에서만 도출되었습니다. 가정에 기반하므로 확정 시 유의해주세요.",
  parse_ok: true,
  raw_markdown: null,
  questions: [
    {
      number: 1,
      category: "Positioning",
      text: "이 제품을 시장(조직 내)에서 어떻게 포지셔닝하시겠습니까?",
      answer: "A",
      options: [
        { letter: "A", text: "사내 특화 전문 도구(Niche Specialist) — 면세 기획전 운영에 특화", is_other: false, recommended: true },
        { letter: "B", text: "플랫폼(Platform) — 다른 MD 업무까지 확장하는 기반 도구", is_other: false, recommended: false },
        { letter: "C", text: "프리미엄(Premium) — 하이엔드 의사결정 지원 도구", is_other: false, recommended: false },
        other("X"),
      ],
    },
    {
      number: 2,
      category: "Positioning",
      text: "한 문장으로 이 제품의 가치 제안(Value Proposition)을 정의한다면?",
      answer: "A",
      options: [
        { letter: "A", text: "분산된 데이터를 통합 분석해 표준화된 후보와 카피를 제공하는 MD 전용 AI 어시스턴트", is_other: false, recommended: true },
        { letter: "B", text: "베테랑 MD의 노하우를 형식지화하는 도구", is_other: false, recommended: false },
        { letter: "C", text: "매출·회전율 데이터를 실시간 반영해 상품 누락 없는 기획전을 보장하는 도구", is_other: false, recommended: false },
        other("X"),
      ],
    },
    // Questions 3–13: same shape (single-select A/B/C + X-Other), category one of
    // Positioning / Differentiation / Business Model / Success Metrics / Risks.
    // Implementer copies remaining questions from the pilot file verbatim; the
    // tests below only assert on Q1/Q2 + aggregate counts, so the exact prose of
    // 3–13 is not load-bearing — but all 13 MUST be present with is_other on the
    // final option and the pilot [Answer] values ("A" for most).
    ...([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13] as const).map((n) => ({
      number: n,
      category:
        n <= 3 ? "Positioning" : n <= 5 ? "Differentiation" : n <= 7 ? "Business Model" : n <= 10 ? "Success Metrics" : "Risks",
      text: `pilot1 strategy-questions.md Question ${n} 본문`,
      answer: n === 12 ? "A,B" : n === 11 ? "C" : "A",
      options: [
        { letter: "A", text: `Q${n} 옵션 A`, is_other: false, recommended: true },
        { letter: "B", text: `Q${n} 옵션 B`, is_other: false, recommended: false },
        { letter: "C", text: `Q${n} 옵션 C`, is_other: false, recommended: false },
        other("X"),
      ],
    })),
  ],
};
