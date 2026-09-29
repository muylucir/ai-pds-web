// frontend/components/canvas/ContextMeter.tsx — 입력창 위의 "컨텍스트 N% 남음".
//
// 에이전트의 컨텍스트 창이 **자동 압축까지** 얼마나 남았는지 보인다(값의 정의는
// lib/api/types.ts의 ContextUsage). 압축은 사용자에게 보이지 않는 사건이지만 결과는
// 보인다 — 에이전트가 앞에서 합의한 세부를 잊기 시작한다. 그 전에 "새로 시작할지"를
// 판단할 근거를 주는 것이 이 표시의 목적이다.
//
// 워크스페이스와 빌드 패널이 같은 컴포넌트를 쓴다(ChatInput이 그린다). 값이 없으면
// 아무것도 그리지 않는다 — 첫 턴 전, 또는 새 대화가 시작될 참일 때다.
"use client";
import type { ContextUsage } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

/** 285000 → "285K", 1000000 → "1M". 표시용이라 정밀도는 필요 없다. */
function compact(tokens: number): string {
  if (tokens >= 1_000_000) return `${+(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

export function ContextMeter({ usage }: { usage: ContextUsage }) {
  const t = useT();
  const left = usage.left_pct;
  // 25% 아래부터 눈에 띄게 — 압축 직전의 한두 턴은 되돌릴 수 없는 결정을 할 시점이다.
  const tone = left < 10 ? "text-red-600" : left < 25 ? "text-amber-600" : "text-slate-400";
  const detail = t("chat.contextDetail")
    .replace("{used}", compact(usage.total_tokens))
    .replace("{limit}", compact(usage.compact_at_tokens));
  return (
    <p className={`text-[11px] text-right mb-1 ${tone}`} title={detail} aria-label={detail}>
      {t("chat.contextLeft").replace("{n}", String(left))}
    </p>
  );
}
