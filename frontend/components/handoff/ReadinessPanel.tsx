// 인계 준비 상태 — 서버 판정(handoff/readiness)을 그대로 보여 준다.
//
// 막는 것은 하나뿐이다: 명세가 없으면 만들 것이 없다(blockers). 나머지는 경고다 — 빈
// 섹션은 "재료 없음"으로 남고, AI 제안 수락은 근거 등급으로 드러난다. 숨기지 않고 보여
// 주는 것이 이 화면의 일이다.
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
  readiness, questionCount, generating, hasPackage, onSupplement, onGenerate, onViewPackage,
}: {
  readiness: Readiness;
  /** 보완 문항 수. 0이면 보완 질문으로 가는 길을 내지 않는다. */
  questionCount: number;
  generating: boolean;
  hasPackage: boolean;
  onSupplement: () => void;
  onGenerate: () => void;
  onViewPackage: () => void;
}) {
  const t = useT();
  const r = readiness;
  const gaps = r.sections.filter((s) => s.status === "partial" || s.status === "missing");
  const noEnvision = r.origin === "B" || r.origin === "EP1";
  const blocked = r.blockers.length > 0;

  const validation = r.prototypes.length === 0
    ? t("handoff.validation.none")
    : r.prototypes.map((p) => `${p.id}: ${t(`handoff.validation.${p.validation}` as keyof Dict)}`).join(" · ");
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
        <Row tone={validationTone} name={t("handoff.check.validation")}
             hint={t("handoff.check.validation.hint")} value={validation} />
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
        <button type="button" onClick={onGenerate} disabled={blocked || generating}
                className="px-5 py-2.5 rounded-lg bg-violet-600 text-white text-sm font-bold hover:bg-violet-700 disabled:opacity-45 disabled:cursor-not-allowed">
          {hasPackage ? t("handoff.regenerate") : t("handoff.generate")}
        </button>
        <span className="text-[13px] text-slate-500">
          {generating ? t("handoff.generating") : t("handoff.generateNote")}
        </span>
        {hasPackage && !generating && (
          <button type="button" onClick={onViewPackage} className="ml-auto text-[13px] text-violet-700 hover:underline">
            {t("handoff.pkg.view")}
          </button>
        )}
      </div>

      <div className="mt-6 text-[13px] text-slate-500">
        <b className="text-slate-700">{t("handoff.contains.title")}</b>
        <ul className="mt-1.5 pl-5 list-disc space-y-0.5">
          <li>{t("handoff.contains.prd")}</li>
          <li>{t("handoff.contains.validation")}</li>
          <li>{t("handoff.contains.scope")}</li>
          <li>{t("handoff.contains.originals")}</li>
        </ul>
        <p className="mt-1">{t("handoff.contains.noSource")}</p>
      </div>
    </section>
  );
}
