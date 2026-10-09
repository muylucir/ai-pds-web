// 만든 패키지 — 상태, 파일, 검사 결과, 만든 뒤 바뀐 원본.
//
// 편집 버튼이 없다. 패키지는 원본에서 유도한 생성물이라 고치는 길은 원본(워크스페이스)이나
// 보완 답을 바꾸고 다시 만드는 것뿐이다 — 여기서 고치면 다음 생성이 그 수정을 덮는다.
"use client";
import { useState } from "react";
import { Markdown } from "@/components/Markdown";
import type { PackageView } from "@/lib/api/handoff";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

/** 파일 목록의 순서. 읽는 순서와 같다(README 참조). */
const ORDER = ["README.md", "PRD.md", "validation-report.md", "build-scope.md"];

/** `2026-10-09T10:40:12Z` → `2026-10-09 10:40 UTC`. 서버는 UTC ISO로 찍는다. */
export function stamp(iso: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]} UTC` : iso;
}

export function PackagePanel({
  pkg, generating, onRegenerate, onDownload, onSupplement, onStatus,
}: {
  pkg: PackageView;
  generating: boolean;
  onRegenerate: () => void;
  onDownload: () => void;
  onSupplement: () => void;
  onStatus: () => void;
}) {
  const t = useT();
  const m = pkg.manifest;
  const names = ORDER.filter((n) => n in pkg.files);
  const [selected, setSelected] = useState("PRD.md");
  const current = names.includes(selected) ? selected : names[0];

  if (!m) return null;
  if (m.status === "failed" || m.status === "interrupted") {
    const reason = t(`handoff.pkg.error.${m.error ?? "generation_failed"}` as keyof Dict)
      ?? t("handoff.pkg.error.generation_failed");
    return (
      <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900 flex items-center justify-between gap-3">
        <span>{m.status === "interrupted" ? t("handoff.pkg.interrupted") : t("handoff.pkg.failed").replace("{reason}", reason)}</span>
        <button type="button" onClick={onRegenerate} disabled={generating}
                className="px-3 py-1.5 rounded-lg border border-rose-300 bg-white text-xs disabled:opacity-45">
          {t("handoff.regenerate")}
        </button>
      </div>
    );
  }
  if (m.status !== "ready") return null;

  const stale = pkg.stale.length > 0 || pkg.supplement_changed;
  const tech = m.findings.filter((f) => f.kind === "tech");
  const vague = m.findings.filter((f) => f.kind === "vague");
  const inFile = m.findings.filter((f) => f.file === current);

  return (
    <section>
      <div className="flex items-center justify-end gap-2 mb-3">
        <button type="button" onClick={onStatus} className="mr-auto text-[13px] text-violet-700 hover:underline">
          {t("handoff.pkg.toStatus")}
        </button>
        <span className="text-xs text-slate-500 mr-1.5">
          {t("handoff.pkg.stamp").replace("{time}", stamp(m.finished_at ?? m.started_at))} ·{" "}
          <b className={stale ? "text-amber-700" : "text-emerald-700"}>
            {stale ? t("handoff.pkg.staleBadge") : t("handoff.pkg.fresh")}
          </b>
        </span>
        <button type="button" onClick={onRegenerate} disabled={generating}
                className={`px-3 py-1.5 rounded-lg border text-[13px] disabled:opacity-45 ${
                  stale || m.findings.length ? "border-amber-400 bg-amber-50 text-amber-700 font-bold" : "border-slate-300 text-slate-600"}`}>
          {t("handoff.regenerate")}
        </button>
        <button type="button" onClick={onDownload}
                className="px-3.5 py-2 rounded-lg bg-violet-600 text-white text-[13px] font-bold hover:bg-violet-700">
          {t("handoff.pkg.download")}
        </button>
      </div>

      {pkg.stale.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 mb-3 text-[13px] text-amber-900">
          <b className="block">{t("handoff.pkg.stale").replace("{n}", String(pkg.stale.length))}</b>
          {pkg.stale.map((p) => <code key={p} className="mr-1.5 text-xs">{p.replace("aiplc-docs/", "")}</code>)}
          <span className="block mt-1">{t("handoff.pkg.regenerateHint")}</span>
        </div>
      )}
      {pkg.supplement_changed && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 mb-3 text-[13px] text-amber-900">
          <b>{t("handoff.pkg.supplementChanged")}</b> {t("handoff.pkg.regenerateHint")}
        </div>
      )}
      {m.findings.length > 0 && (
        <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 mb-3 text-[13px] text-rose-900">
          <b className="block">
            {t("handoff.pkg.findings").replace("{tech}", String(tech.length)).replace("{vague}", String(vague.length))}
          </b>
          {t("handoff.pkg.findingsHint")}
        </div>
      )}
      {m.truncated.length > 0 && (
        <p className="mb-3 text-xs text-slate-500">
          {t("handoff.pkg.truncated").replace("{n}", String(m.truncated.length))}
        </p>
      )}

      <div className="grid lg:grid-cols-[minmax(240px,20%)_1fr] gap-6 items-start">
        <aside className="bg-white rounded-xl border border-slate-200 p-3.5">
          <h4 className="mb-2 text-[11px] tracking-wide uppercase text-slate-400">{t("handoff.pkg.files")}</h4>
          <ul className="mb-4">
            {names.map((n) => (
              <li key={n}>
                <button type="button" onClick={() => setSelected(n)} aria-current={n === current ? "page" : undefined}
                        className={`w-full text-left px-2.5 py-1.5 rounded-lg text-[13px] ${
                          n === current ? "bg-violet-50 text-violet-700 font-semibold" : "text-slate-700 hover:bg-slate-100"}`}>
                  {n}
                </button>
              </li>
            ))}
          </ul>
          <div className="border-t border-slate-100 pt-3 text-[13px] space-y-1.5">
            <div className="flex justify-between">
              <span>{t("handoff.pkg.lint.tech")}</span>
              <span className={tech.length ? "text-rose-700 font-semibold" : "text-emerald-700 font-semibold"}>
                {tech.length ? `! ${tech.length}` : "✓ 0"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>{t("handoff.pkg.lint.vague")}</span>
              <span className={vague.length ? "text-rose-700 font-semibold" : "text-emerald-700 font-semibold"}>
                {vague.length ? `! ${vague.length}` : "✓ 0"}
              </span>
            </div>
          </div>
          <button type="button" onClick={onSupplement} className="mt-4 text-xs text-violet-700 hover:underline">
            {t("handoff.check.answerSupplement")}
          </button>
        </aside>

        <article className="bg-white rounded-xl border border-slate-200 px-6 py-5">
          <div className="flex justify-between items-center mb-3">
            <h3 className="text-[17px] font-semibold text-slate-900">{current}</h3>
            <span className="text-xs text-slate-400">{t("handoff.pkg.readOnly")}</span>
          </div>
          {inFile.length > 0 && (
            <ul className="mb-4 text-xs text-rose-800 space-y-0.5">
              {inFile.map((f, i) => (
                <li key={i}>
                  L{f.line} · {t(`handoff.pkg.findingKind.${f.kind}` as keyof Dict)} · <code>{f.term}</code>
                </li>
              ))}
            </ul>
          )}
          {current && <Markdown text={pkg.files[current]} />}
          <p className="mt-4 pt-3 border-t border-slate-100 text-xs text-slate-400">{t("handoff.pkg.foot")}</p>
        </article>
      </div>
    </section>
  );
}
