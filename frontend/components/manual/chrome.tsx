"use client";
// frontend/components/manual/chrome.tsx — shell pieces shared by the mockups.
//
// The mockup discipline: **write no new copy.** Buttons, labels, and states are all read from the
// app dictionary via useT(). So (1) manual illustrations don't drift from the real screens,
// (2) there is nothing to translate (both languages already exist), and (3) when UI copy changes, the manual
// changes with it.
//
// What lives here is only the **shape** of those pieces — no new sentences.
import type { ReactNode } from "react";

/** Browser-window shape that wraps the whole mockup. */
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

/** One card/panel cell. */
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

/** Panel title line. */
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

/** A button inside a mockup. It can't actually be pressed (it's a picture). */
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

/** Input-box shape. `value` takes the dictionary's placeholder as-is. */
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
 * Gray bars used instead of real sentences. Putting prose in a mockup makes that prose a
 * translation target and leaves room to drift from the screen — show only the **place** of a paragraph.
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
