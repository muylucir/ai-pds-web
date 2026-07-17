import type { QuestionFile } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/discovery/envision/prfaq-clarification-questions.md.
// A clarification file is just another *-questions.md; the wizard renders it the
// same way, and its mere presence in GET /questions triggers the banner.
export const clarificationQuestions: QuestionFile = {
  name: "prfaq-clarification-questions.md",
  preamble: "응답에서 하나의 모순을 발견했습니다. 아래 질문으로 확인해주세요.",
  parse_ok: true,
  raw_markdown: null,
  questions: [
    {
      number: 1,
      category: "Contradiction 1: 응답 시간 제약 (30초)",
      text: '실제 서비스의 응답 시간 목표(SLA)는 어떻게 하시겠습니까?',
      answer: "C",
      options: [
        { letter: "A", text: "30초 이내 응답 목표는 그대로 유지 — 실패 요인 목록에서만 제외", is_other: false, recommended: false },
        { letter: "B", text: "30초 제약을 완화 — 새로운 목표 응답 시간을 알려주세요", is_other: false, recommended: false },
        { letter: "C", text: "아직 정하지 않음 — 파일럿 운영 중 데이터로 결정", is_other: false, recommended: false },
        { letter: "X", text: "Other (please describe after [Answer]: tag below)", is_other: true, recommended: false },
      ],
    },
  ],
};
