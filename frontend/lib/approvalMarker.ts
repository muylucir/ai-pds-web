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

// ---- 채팅으로 답한 승인 (게이트 문맥 안에서만) ----
//
// 게이트 버튼은 항상 approvalTurnText()를 보내므로 위 판정식으로 충분하다.
// 그런데 사용자는 **채팅으로도 승인한다** — 워크스페이스 하단이 자유 입력을
// 권하고, 에이전트가 승인을 객관식으로 물으면 letter로 답한다.
//
// 실측(pilot1의 audit.md): 승인 게이트 5건 중 3건이 이 형태여서 인식되지
// 않았다 — idx=41 "동의"(**최종 승인**), idx=33 "진행", idx=17 "A".
//
// 이 목록을 문맥 제한 없이 쓰면 안 된다. 평범한 대화의 "진행"이 결정으로
// 세어지면 PM이 누르기 전에 게이트가 사라진다 — approvalState의 그 테스트가
// 지키는 불변식이다. 그래서 `context`가 승인 게이트를 가리킬 때만 적용한다.
// pilot1 로그에서 그 문맥은 정확히 승인 게이트만 가리켰다.
const CHAT_APPROVAL_RE = /^\s*(승인|동의|진행|좋아요?|네|예|확인|[A-F]|Approved?|Agreed?|Proceed|Yes|OK)\s*$/i;

//: `context`가 승인 게이트인지. 실측 로그의 표기를 그대로 받는다:
//: "Envision — Step 6 Approval Gate", "Discovery Phase Complete — Final Approval",
//: "최종 승인".
const GATE_CONTEXT_RE = /approval\s*gate|final\s*approval|최종\s*승인|승인\s*게이트/i;

/** 이 항목이 **승인 게이트 문맥에서** 사용자가 채팅으로 승인한 것인가.
 *
 *  레코드(approval_store)가 없는 기존 프로젝트를 위한 폴백이다. 레코드가
 *  있으면 이 판정은 쓰이지 않는다 — 그쪽이 훨씬 정확하다.
 */
export function isChatApprovalInGateContext(text: string, context: string): boolean {
  if (!GATE_CONTEXT_RE.test(context)) return false;
  return CHAT_APPROVAL_RE.test(text);
}
