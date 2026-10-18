// Handoff readiness — shows the server's assessment (handoff/readiness) as-is.
//
// Only one thing blocks: without a spec there is nothing to build (blockers). Everything else is a warning — empty
// sections stay as "no material", and accepted AI suggestions surface as an evidence grade. Showing rather than hiding
// them is this screen's job.
//
// Spec consistency is a warning too. Reflecting what changed during the build into the spec is done by Discovery's reconciliation
// pass (discovery-config/CLAUDE.md), so here we send the user back there — filling a draft into the workspace
// input (same approach as reflecting a survey).
"use client";
import type { Readiness, SectionKey } from "@/lib/api/handoff";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

type Tone = "ok" | "warn" | "info" | "no";

const ICON: Record<Tone, { mark: string; cls: string }> = {
  ok: { mark: "✓", cls: "bg-emerald-500" },
  warn: { mark: "!", cls: "bg-amber-500" },
  info: { mark: "i", cls: "bg-sky-400" },
  no: { mark: "✕", cls: "bg-rose-600" },
};

function Row({ tone, name, hint, value, required, action }: {
  tone: Tone; name: string; hint?: string; value: string; required?: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="grid grid-cols-[28px_230px_1fr_auto] items-center gap-2 px-5 py-3 border-t border-slate-100 text-sm">
      <span className={`inline-flex w-5 h-5 rounded-full items-center justify-center text-[11px] font-bold text-white ${ICON[tone].cls}`}>
        {ICON[tone].mark}
      </span>
      <span className="font-semibold text-slate-900">
        {name}
        {required && (
          <span className="ml-1.5 text-[10px] font-bold text-rose-700 bg-rose-50 border border-rose-200 px-1.5 rounded-full align-middle">
            {required}
          </span>
        )}
        {hint && <small className="block font-normal text-[11px] text-slate-400">{hint}</small>}
      </span>
      <span className="text-[13px] text-slate-600">{value}</span>
      {action ? (
        <button type="button" onClick={action.onClick} className="text-[13px] text-violet-700 hover:underline whitespace-nowrap">
          {action.label}
        </button>
      ) : <span />}
    </div>
  );
}

export function sectionLabel(t: (k: keyof Dict) => string, key: SectionKey): string {
  return t(`handoff.section.${key}` as keyof Dict);
}

export function ReadinessPanel({
  readiness, questionCount, openDecisions = null, generating, hasPackage, onSupplement, onGenerate,
  onViewPackage, onReconcile, readOnly = false,
}: {
  readiness: Readiness;
  /** Number of supplementary questions. If 0, no path to the supplementary questions is offered. */
  questionCount: number;
  /** Number of the last PRD's open questions the PM has not answered yet. null if there is no package.
   *  Section assessment looks at file existence, so even with "0 empty sections" a decision blocking implementation may be open. */
  openDecisions?: number | null;
  generating: boolean;
  hasPackage: boolean;
  onSupplement: () => void;
  onGenerate: () => void;
  onViewPackage: () => void;
  /** Go to the workspace chat and fill `draft` into the input. */
  onReconcile: (draft: string) => void;
  /** Viewer member — sees the assessment, but no generate button. */
  readOnly?: boolean;
}) {
  const t = useT();
  const r = readiness;
  const gaps = r.sections.filter((s) => s.status === "partial" || s.status === "missing");
  const noEnvision = r.origin === "B" || r.origin === "EP1";
  const blocked = r.blockers.length > 0;

  const validation = r.prototypes.length === 0
    ? t("handoff.validation.none")
    : r.prototypes.map((p) => `${p.id}: ${t(`handoff.validation.${p.validation}` as keyof Dict)}`).join(" · ");
  const histories = r.prototypes.filter((p) => p.reconcile !== "not_needed");
  const unreconciled = histories.filter((p) => p.reconcile === "missing" || p.reconcile === "stale");
  const reconcileDraft = t("handoff.reconcile.draft")
    .replace("{list}", unreconciled.map((p) => p.history ?? p.id).join(", "));

  const validationTone: Tone = r.prototypes.some((p) => p.validation === "validated" || p.validation === "survey")
    ? "ok" : "info";

  return (
    <section className="max-w-[920px]">
      <div className="bg-white border border-slate-200 rounded-xl py-1.5">
        <h3 className="px-5 pt-3 pb-2 text-[15px] font-semibold text-slate-900">{t("handoff.readiness.title")}</h3>
        <div className="mx-5 mb-2 px-3.5 py-2.5 rounded-lg bg-sky-50 border border-sky-200 text-[13px] text-sky-800">
          <b className="text-sky-900">
            {t("handoff.origin.label")}: {r.origin ? t(`handoff.origin.${r.origin}` as keyof Dict) : t("handoff.origin.none")}
          </b>
          {noEnvision && <> · {t("handoff.origin.noEnvision")}</>}
        </div>
        <Row
          tone={blocked ? "no" : "ok"}
          name={t("handoff.check.spec")}
          hint={t("handoff.check.spec.hint")}
          required={t("handoff.check.required")}
          value={blocked ? t("handoff.check.spec.none")
            : t("handoff.check.spec.ok").replace("{n}", String(r.prototypes.length))}
        />
        <Row
          tone={gaps.length ? "warn" : "ok"}
          name={t("handoff.check.sections")}
          hint={t("handoff.check.sections.hint")}
          value={gaps.length
            ? t("handoff.check.sections.some").replace("{n}", String(gaps.length))
              .replace("{list}", gaps.map((s) => `${s.number} ${sectionLabel(t, s.key)}`).join(" · "))
            : t("handoff.check.sections.none")}
          action={questionCount > 0 ? { label: t("handoff.check.answerSupplement"), onClick: onSupplement } : undefined}
        />
        {openDecisions !== null && (
          <Row
            tone={openDecisions ? "warn" : "ok"}
            name={t("handoff.check.open")}
            hint={t("handoff.check.open.hint")}
            value={openDecisions ? t("handoff.check.open.some").replace("{n}", String(openDecisions))
              : t("handoff.check.open.none")}
            action={openDecisions ? { label: t("handoff.check.answerSupplement"), onClick: onSupplement } : undefined}
          />
        )}
        <Row tone={validationTone} name={t("handoff.check.validation")}
             hint={t("handoff.check.validation.hint")} value={validation} />
        {histories.length > 0 && (
          <Row
            tone={unreconciled.length ? "warn" : "ok"}
            name={t("handoff.check.reconcile")}
            hint={t("handoff.check.reconcile.hint")}
            value={unreconciled.length
              ? t("handoff.check.reconcile.needed").replace("{list}", unreconciled
                .map((p) => `${p.id}: ${t(`handoff.reconcile.${p.reconcile}` as keyof Dict)}`).join(" · "))
              : t("handoff.check.reconcile.current")}
            action={unreconciled.length && !readOnly
              ? { label: t("handoff.check.reconcile.action"), onClick: () => onReconcile(reconcileDraft) }
              : undefined}
          />
        )}
        <Row
          tone={r.broken_references.length ? "warn" : "ok"}
          name={t("handoff.check.refs")}
          hint={t("handoff.check.refs.hint")}
          value={!r.discovery_document ? t("handoff.check.refs.noDocument")
            : r.broken_references.length
              ? t("handoff.check.refs.broken").replace("{n}", String(r.broken_references.length))
                .replace("{list}", r.broken_references.join(", "))
              : t("handoff.check.refs.ok")}
        />
        <Row
          tone="info"
          name={t("handoff.check.ai")}
          hint={t("handoff.check.ai.hint")}
          value={t("handoff.check.ai.value").replace("{accepted}", String(r.ai_defaults.accepted))
            .replace("{suggested}", String(r.ai_defaults.suggested))}
          action={r.ai_defaults.accepted > 0
            ? { label: t("handoff.check.ai.confirm"), onClick: onSupplement } : undefined}
        />
      </div>

      <div className="flex items-center gap-3.5 mt-5">
        {!readOnly && (<>
        <button type="button" onClick={onGenerate} disabled={blocked || generating}
                className="px-5 py-2.5 rounded-lg bg-violet-600 text-white text-sm font-bold hover:bg-violet-700 disabled:opacity-45 disabled:cursor-not-allowed">
          {hasPackage ? t("handoff.regenerate") : t("handoff.generate")}
        </button>
        <span className="text-[13px] text-slate-500">
          {generating ? t("handoff.generating") : t("handoff.generateNote")}
        </span>
        </>)}
        {hasPackage && !generating && (
          <button type="button" onClick={onViewPackage} className="ml-auto text-[13px] text-violet-700 hover:underline">
            {t("handoff.pkg.view")}
          </button>
        )}
      </div>

      <div className="mt-6 text-[13px] text-slate-500">
        <b className="text-slate-700">{t("handoff.contains.title")}</b>
        <ul className="mt-1.5 pl-5 list-disc space-y-0.5">
          <li>{t("handoff.contains.document")}</li>
          <li>{t("handoff.contains.open")}</li>
          <li>{t("handoff.contains.ledger")}</li>
          <li>{t("handoff.contains.originals")}</li>
        </ul>
        <p className="mt-1">{t("handoff.contains.noSource")}</p>
      </div>
    </section>
  );
}
