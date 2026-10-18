// frontend/lib/startMessage.ts — the opening sentence the start card sends to the agent.
//
// **It takes the project language, not the UI locale.** The same judgment as approvalMarker.ts:
// this string is not screen text; it goes to the agent as the first user utterance in the workspace
// chat and remains in the transcript as a user bubble. If a Korean project is started from the English
// UI, that project's conversation must proceed in Korean, so a Korean sentence must be sent. Only the
// button labels (welcome.*) are translated into the UI language.
//
// That is why the card's labels and this sentence are not kept in one file — the labels come from the
// dictionary (UI language) and this sentence from here (project language); their sources differ.
import type { Locale } from "@/lib/i18n";

/** Opening sentence for Path A (start from customer pain points). */
const PATH_A: Record<Locale, string> = {
  ko: "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.",
  en: "Let's start AI-PLC. I want to go with Path A (start from customer pain points).",
};

/** Opening sentence for Path B (start from an already defined use case). */
const PATH_B: Record<Locale, string> = {
  ko: "AI-PLC를 시작해줘. Path B(이미 정리된 유스케이스에서 시작)로 진행하고 싶어.",
  en: "Let's start AI-PLC. I want to go with Path B (start from use cases we already have).",
};

export function startMessage(path: "A" | "B", language: Locale): string {
  return path === "A" ? PATH_A[language] : PATH_B[language];
}
