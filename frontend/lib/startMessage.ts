// frontend/lib/startMessage.ts — 시작 카드가 에이전트에게 보내는 개시 문장.
//
// **UI 로케일이 아니라 프로젝트 언어를 받는다.** approvalMarker.ts와 같은
// 판단이다: 이 문자열은 화면 문구가 아니라 workspace 채팅의 첫 사용자 발화로
// 에이전트에게 가고, 트랜스크립트에 사용자 말풍선으로 남는다. 영어 UI로 한국어
// 프로젝트를 시작하면 그 프로젝트의 대화는 한국어로 진행되어야 하므로 한국어
// 문장이 가야 한다. 버튼 라벨(welcome.*)만 UI 언어로 번역된다.
//
// 카드의 라벨과 이 문장을 한 파일에 두지 않는 이유가 그것이다 — 라벨은
// 딕셔너리(UI 언어), 이 문장은 여기(프로젝트 언어)로 출처가 다르다.
import type { Locale } from "@/lib/i18n";

/** Path A(고객 페인 포인트에서 시작) 개시 문장. */
const PATH_A: Record<Locale, string> = {
  ko: "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.",
  en: "Let's start AI-PLC. I want to go with Path A (start from customer pain points).",
};

/** Path B(이미 정리된 유스케이스에서 시작) 개시 문장. */
const PATH_B: Record<Locale, string> = {
  ko: "AI-PLC를 시작해줘. Path B(이미 정리된 유스케이스에서 시작)로 진행하고 싶어.",
  en: "Let's start AI-PLC. I want to go with Path B (start from use cases we already have).",
};

export function startMessage(path: "A" | "B", language: Locale): string {
  return path === "A" ? PATH_A[language] : PATH_B[language];
}
