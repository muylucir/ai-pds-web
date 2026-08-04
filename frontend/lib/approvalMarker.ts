// frontend/lib/approvalMarker.ts — 승인 턴 텍스트와 판정식의 단일 출처.
//
// 이 두 가지가 어긋나면 승인 게이트가 조용히 열리지 않는다: 프론트가 보낸
// 텍스트를 감사 로그에서 되찾지 못하고, 사용자는 버튼을 눌렀는데 아무 일도
// 일어나지 않은 것으로 본다. 그래서 한 파일에 둔다.
//
// 왜 불투명 마커(`[APPROVED]`)가 아닌가: 이 텍스트는 기계 신호가 아니다.
// review/page.tsx의 sendTurn이 postMessage로 이것을 에이전트에게 보내고, 그
// 턴은 트랜스크립트와 채팅 히스토리에 사용자 말풍선으로 남는다. 에이전트가
// 승인으로 이해해야 하고 사람이 읽어야 한다. 프로젝트 언어의 승인 단어는
// 에이전트가 이미 그 언어로 대화하고 있으므로 추가 프롬프트 지원 없이 통한다.
//
// 상류 AI-PLC 룰은 "user explicitly approves"만 요구하고 키워드를 정의하지
// 않는다(envision.md:412, product-strategy.md:154, go-to-market.md:160) —
// 즉 이 단어는 우리가 정한 프로토콜이고, 그래서 우리가 두 언어를 다 다뤄야 한다.
import type { Locale } from "@/lib/i18n";

const TURN_TEXT: Record<Locale, string> = { ko: "승인", en: "Approved" };

/**
 * 승인 턴으로 보낼 텍스트. **UI 로케일이 아니라 프로젝트 언어**를 받는다.
 *
 * 영어 UI로 한국어 프로젝트를 승인하면 대화는 한국어로 진행되고 있으므로
 * "승인"이 가야 한다. 버튼 라벨만 UI 언어로 번역된다.
 */
export function approvalTurnText(language: Locale): string {
  return TURN_TEXT[language];
}

// 두 언어를 다 받는다. 기존 한국어 감사 로그가 계속 인식되어야 하고,
// parsers/audit.py가 `사용자 입력|User Raw Input`을 둘 다 받는 것과 같은 규율이다.
//
// 영어에 `i` 플래그를 주는 이유: 감사 로그는 에이전트가 사용자 입력을 옮겨
// 적은 것이라 표기가 흔들린다("approved", "Approved"). 한국어에는 대소문자가
// 없어 영향이 없다.
//
// `^...$`로 묶어 전체 일치를 요구한다 — 문장 속의 언급을 결정으로 세면 PM이
// 누르기 전에 게이트가 사라진다(approvalState.test.ts의 그 테스트).
const APPROVAL_RE = /^\s*(승인|Approved)\s*$/i;

/** 이 감사 로그 항목의 user_input이 승인 결정인가. */
export function isApprovalText(text: string): boolean {
  return APPROVAL_RE.test(text);
}
