"use client";
// frontend/components/manual/chrome.tsx — 목업이 공유하는 껍데기 조각들.
//
// 목업의 규율: **문구를 새로 쓰지 않는다.** 버튼·라벨·상태는 전부 useT()로
// 앱 딕셔너리에서 읽는다. 그래서 (1) 매뉴얼 그림이 실제 화면과 어긋나지 않고,
// (2) 번역할 것이 없고(두 언어가 이미 있다), (3) UI 문구가 바뀌면 매뉴얼도
// 같이 바뀐다.
//
// 여기 있는 것은 그 조각들의 **모양**만이다 — 새 문장은 없다.
import type { ReactNode } from "react";

/** 목업 전체를 감싸는 브라우저 창 모양. */
export function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-300 bg-white shadow-sm">
      <div className="flex items-center gap-1.5 border-b border-slate-200 bg-slate-100 px-3 py-2">
        <span className="h-2.5 w-2.5 rounded-full bg-rose-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
      </div>
      <div className="p-3 text-[11px] leading-tight text-slate-700">{children}</div>
    </div>
  );
}

/** 카드·패널 한 칸. */
export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-2.5 ${className}`}>
      {children}
    </div>
  );
}

/** 패널 제목 줄. */
export function PanelTitle({ children }: { children: ReactNode }) {
  return <p className="mb-1.5 text-[10px] font-semibold text-slate-500">{children}</p>;
}

export type ButtonTone = "primary" | "ghost" | "danger" | "quiet";

const TONE: Record<ButtonTone, string> = {
  primary: "bg-violet-600 text-white",
  ghost: "border border-slate-300 bg-white text-slate-600",
  danger: "border border-rose-200 bg-rose-50 text-rose-700",
  quiet: "bg-slate-100 text-slate-500",
};

/** 목업 안의 버튼. 실제로 눌리지 않는다(그림이다). */
export function Btn({
  children,
  tone = "ghost",
}: {
  children: ReactNode;
  tone?: ButtonTone;
}) {
  return (
    <span className={`inline-block rounded-md px-2 py-1 text-[10px] font-medium ${TONE[tone]}`}>
      {children}
    </span>
  );
}

export type BadgeTone = "violet" | "emerald" | "amber" | "slate" | "rose";

const BADGE: Record<BadgeTone, string> = {
  violet: "bg-violet-50 text-violet-700 border-violet-200",
  emerald: "bg-emerald-50 text-emerald-700 border-emerald-200",
  amber: "bg-amber-50 text-amber-700 border-amber-200",
  slate: "bg-slate-50 text-slate-600 border-slate-200",
  rose: "bg-rose-50 text-rose-700 border-rose-200",
};

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: BadgeTone }) {
  return (
    <span
      className={`inline-block rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${BADGE[tone]}`}
    >
      {children}
    </span>
  );
}

/** 입력창 모양. `value`는 딕셔너리의 placeholder를 그대로 받는다. */
export function Field({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <label className="block">
      <span className="text-[10px] font-medium text-slate-500">{label}</span>
      <span className="mt-0.5 block rounded-md border border-slate-300 px-2 py-1 text-[10px] text-slate-400">
        {value}
      </span>
    </label>
  );
}

/**
 * 실제 문장 대신 쓰는 회색 줄. 목업에 산문을 넣으면 그 산문이 번역 대상이
 * 되고, 화면과 어긋날 여지가 생긴다 — 문단의 **자리**만 보여 준다.
 */
export function Lines({ n = 3, className = "" }: { n?: number; className?: string }) {
  return (
    <span className={`block space-y-1 ${className}`} aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <span
          key={i}
          className="block h-1.5 rounded bg-slate-200"
          style={{ width: `${100 - (i % 3) * 18}%` }}
        />
      ))}
    </span>
  );
}
