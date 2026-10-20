// Supplementary questions — ask only where there is no material (the conditions in the server's handoff/supplement header). Fixed
// questions for empty sections, plus the PM decisions the last PRD left as open questions that must be settled before the build.
// Decisions that can be settled during the build are collapsed — the PRD has written down how to build in the meantime.
//
// Questions are not pre-filled with AI defaults. In measurement, PMs picked the displayed default 89% of the time —
// giving a default turns this answer into "accepting the AI suggestion" again. For pre-build decisions, the server gives
// options grounded in the sources without a recommendation, and the screen preselects nothing. With no options, it is a
// blank field. Either way, there are "직접 입력" (enter my own) and "모름" (don't know).
//
// Confirming does not change content. The only way to change it is the workspace — letting it be edited here would leave
// the same decision recorded differently in two places: the Discovery artifacts and the supplementary answers.
"use client";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { STAGE_ORDER } from "@/lib/api/handoff";
import type {
  Confirmation, OpenOption, OpenQuestion, SupplementQuestion, SupplementQuestionId, SupplementUpdate,
  SupplementView,
} from "@/lib/api/handoff";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import { sectionLabel } from "./ReadinessPanel";

/** `custom` is screen state — "직접 입력" was chosen on a question with options. It is not saved.
 *  `pending_with` is the "who is confirming it" of an external-confirmation question. */
type Draft = Record<string, { text: string; unknown: boolean; custom?: boolean; pending_with?: string }>;

function initialDraft(view: SupplementView): Draft {
  const out: Draft = {};
  for (const q of view.questions) {
    out[q.id] = { text: q.answer?.text ?? "", unknown: q.answer?.unknown ?? false };
  }
  for (const q of openAll(view)) {
    const text = q.answer?.text ?? "";
    out[q.key] = { text, unknown: q.answer?.unknown ?? false,
                   custom: !!text && !q.options.some((o) => o.text === text),
                   pending_with: q.answer?.pending_with ?? "" };
  }
  return out;
}

const openAll = (view: SupplementView) =>
  [...view.open_questions, ...view.open_deferred, ...view.open_answered];

const filled = (d: Draft[string] | undefined) =>
  !!d && (d.unknown || !!d.text.trim() || !!d.pending_with?.trim());

export function toUpdate(view: SupplementView, draft: Draft, confirmed: Set<string>): SupplementUpdate {
  const answers: SupplementUpdate["answers"] = {};
  for (const q of view.questions) {
    const d = draft[q.id];
    if (filled(d)) answers[q.id as SupplementQuestionId] = d;
  }
  // Answers already superseded are sent too. Saving is a replacement, so anything left out is erased from the record.
  for (const q of view.superseded) {
    if (q.answer) answers[q.id] = { text: q.answer.text, unknown: q.answer.unknown };
  }
  // Questions answered earlier and dropped from the PRD are sent for the same reason. What was fixed stays fixed.
  const open_answers: SupplementUpdate["open_answers"] = {};
  for (const q of openAll(view)) {
    const d = draft[q.key];
    if (filled(d)) {
      open_answers[q.key] = { question: q.question, text: d.text, unknown: d.unknown,
                              pending_with: d.pending_with ?? "" };
    }
  }
  return { answers, confirmed: [...confirmed], open_answers };
}

export function SupplementForm({
  view, workspaceHref, busy, message, onSave, onSaveAndGenerate, onBack, readOnly = false,
}: {
  view: SupplementView;
  workspaceHref: string;
  busy: boolean;
  message: string | null;
  onSave: (update: SupplementUpdate) => void;
  onSaveAndGenerate: (update: SupplementUpdate) => void;
  onBack: () => void;
  /** Viewing member. Answers are visible but cannot be edited. */
  readOnly?: boolean;
}) {
  const t = useT();
  const [draft, setDraft] = useState<Draft>(() => initialDraft(view));
  const [confirmed, setConfirmed] = useState<Set<string>>(
    () => new Set(view.confirmations.filter((c) => c.confirmed_at).map((c) => c.key)));

  const groups = useMemo(() => {
    const bySection = new Map<string, SupplementQuestion[]>();
    for (const q of view.questions) {
      bySection.set(q.section, [...(bySection.get(q.section) ?? []), q]);
    }
    return [...bySection.entries()];
  }, [view.questions]);

  // Progress counts only blank questions. Confirmation is optional — with it in the denominator, even after filling
  // in every question it showed "아직 40개 남음" (40 still remaining) (measured ws-g).
  const total = view.questions.length + view.open_questions.length;
  const done = view.questions.filter((q) => filled(draft[q.id])).length
    + view.open_questions.filter((q) => filled(draft[q.key])).length;

  // With a package, the decisions vision.md relies on as unconfirmed AI suggestions come first, and of those the ones in the
  // first-release scope or the success metrics lead — development builds toward them, and in measurement (ws-o) the
  // development harness re-asked the scope but not those targets. The rest are collapsed by stage. Confirmed decisions
  // appear in the decision ledger as "AI 제안 · PM 확인".
  const scope = view.has_package ? view.confirmations.filter((c) => c.in_scope) : [];
  const inVision = view.has_package
    ? view.confirmations.filter((c) => c.in_vision.length && !c.in_scope) : [];
  const stages = useMemo(() => {
    const byStage = new Map<string, Confirmation[]>();
    for (const c of view.confirmations) {
      if (view.has_package && c.in_vision.length) continue;
      byStage.set(c.stage, [...(byStage.get(c.stage) ?? []), c]);
    }
    return STAGE_ORDER.filter((st) => byStage.has(st)).map((st) => [st, byStage.get(st)!] as const);
  }, [view.confirmations, view.has_package]);
  const restCount = stages.reduce((n, [, items]) => n + items.length, 0);

  // One confirmation item. The question, the chosen decision, elaboration, (with a package) the vision.md lines it became,
  // and the AI rationale.
  // Drawn by a function, not a component — a component defined inside render is a new type every time, so the list
  // would remount.
  const renderItem = (c: Confirmation) => {
    const on = confirmed.has(c.key);
    return (
      <li key={c.key} className="px-4 py-3 border-t border-slate-100 first:border-t-0">
        <p className="text-[13px] font-semibold text-slate-900">{c.ask}</p>
        <p className="mt-1 text-[13px] text-slate-800">
          <span className="mr-1.5 text-xs text-slate-400">{t("handoff.supp.confirm.chosen")}</span>
          {c.choices.length ? c.choices.join(" / ") : c.answer}
        </p>
        {c.remark && (
          <p className="mt-0.5 text-xs text-slate-600">
            <span className="mr-1.5 text-slate-400">{t("handoff.supp.confirm.remark")}</span>{c.remark}
          </p>
        )}
        {view.has_package && c.in_vision.map((line) => (
          <p key={line} className="mt-0.5 text-xs text-violet-800">
            <span className="mr-1.5 text-violet-400">{t("handoff.supp.confirm.visionLine")}</span>{line}
          </p>
        ))}
        {c.note && (
          <p className="mt-0.5 text-[11px] text-slate-400">{t("handoff.supp.confirm.note")}: {c.note}</p>
        )}
        <div className="mt-2 flex gap-1.5">
          <button type="button" aria-pressed={on} onClick={() => toggle(c.key)}
                  className={`px-2.5 py-1 rounded-lg border text-xs ${
                    on ? "bg-sky-50 border-sky-200 text-sky-700 font-bold" : "border-slate-300 text-slate-600"}`}>
            {on ? t("handoff.supp.confirm.confirmed") : t("handoff.supp.confirm.yes")}
          </button>
          <Link href={`${workspaceHref}?draft=${encodeURIComponent(reviseDraft(c))}`}
                className="px-2.5 py-1 rounded-lg border border-slate-300 text-xs text-slate-600">
            {t("handoff.supp.confirm.revise")}
          </Link>
        </div>
      </li>
    );
  };

  // The chat draft for changing one decision — the same ?draft= handoff as the review screen's change request. Without it the PM
  // arrived in the workspace with nothing to say which decision, and had to find the question file and number again. The new
  // answer goes last, where the PM types; nothing is sent until the PM sends it.
  const reviseDraft = (c: Confirmation) => t("handoff.supp.confirm.reviseDraft")
    .replace("{file}", c.file.replace(/^aiplc-docs\/discovery\//, ""))
    .replace("{number}", String(c.number))
    .replace("{ask}", c.ask)
    .replace("{answer}", c.choices.length ? c.choices.join(" / ") : c.answer);

  // One answer field. With options, it is one of the options · enter my own · don't know; without, a blank field and don't know.
  const renderAnswer = (id: string, label: ReactNode, hint: string | null,
                        options: OpenOption[] = [], external = false) => {
    const d = draft[id] ?? { text: "", unknown: false };
    const domId = `q-${id.replace(/[^\w-]/g, "-")}`;
    const typing = !options.length || !!d.custom;
    return (
      <div key={id} className={`bg-white border rounded-xl px-4 py-3.5 mb-2.5 ${
        d.unknown ? "border-dashed border-slate-300" : d.text.trim() ? "border-sky-200" : "border-slate-200"}`}>
        {options.length ? (
          <p id={domId} className="text-sm font-semibold text-slate-900">{label}</p>
        ) : (
          <label htmlFor={domId} className="block text-sm font-semibold text-slate-900">{label}</label>
        )}
        {hint && <p className="mb-2 text-xs text-slate-400">{hint}</p>}
        {options.length > 0 && (
          <div role="radiogroup" aria-labelledby={domId} className={`space-y-1 ${hint ? "" : "mt-2"}`}>
            {options.map((o) => (
              <label key={o.text} className="flex items-start gap-2 text-[13px] text-slate-800 cursor-pointer">
                <input type="radio" name={domId} className="mt-0.5" disabled={d.unknown}
                       checked={!d.unknown && !d.custom && d.text === o.text}
                       onChange={() => set(id, { text: o.text, custom: false })} />
                <span>
                  {o.text}
                  <span className="ml-1.5 text-[11px] text-slate-400">{t("handoff.supp.option.source")}: {o.source}</span>
                </span>
              </label>
            ))}
            <label className="flex items-start gap-2 text-[13px] text-slate-800 cursor-pointer">
              <input type="radio" name={domId} className="mt-0.5" disabled={d.unknown}
                     checked={!d.unknown && !!d.custom}
                     onChange={() => set(id, { text: "", custom: true })} />
              {t("handoff.supp.option.custom")}
            </label>
          </div>
        )}
        {typing && (
          <textarea id={options.length ? undefined : domId} aria-label={options.length ? t("handoff.supp.option.custom") : undefined}
                    value={d.unknown ? "" : d.text} disabled={d.unknown}
                    maxLength={4000} rows={options.length ? 2 : 3}
                    onChange={(e) => set(id, { text: e.target.value })}
                    className={`w-full rounded-lg border border-slate-300 px-2.5 py-2 text-[13px] disabled:bg-slate-50 ${hint && !options.length ? "" : "mt-2"}`} />
        )}
        {external && (
          <label className="block mt-2 text-xs text-slate-600">
            {t("handoff.supp.external.pending")}
            <input type="text" value={d.unknown ? "" : d.pending_with ?? ""} disabled={d.unknown}
                   maxLength={200} placeholder={t("handoff.supp.external.pendingPlaceholder")}
                   onChange={(e) => set(id, { pending_with: e.target.value })}
                   className="mt-1 block w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-[13px] disabled:bg-slate-50" />
          </label>
        )}
        <label className="mt-2 inline-flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
          <input type="checkbox" checked={d.unknown}
                 onChange={(e) => set(id, { unknown: e.target.checked })} />
          {t("handoff.supp.unknown")}
        </label>
      </div>
    );
  };
  const openLabel = (q: OpenQuestion) => (
    <>
      {q.id && <span className="mr-1.5 text-xs font-normal text-slate-400">{q.id}</span>}
      {q.external && (
        <span className="mr-1.5 px-1.5 py-0.5 rounded bg-amber-50 border border-amber-200 text-[11px] font-normal text-amber-800">
          {t("handoff.supp.external.badge")}
        </span>
      )}
      {q.question}
    </>
  );

  const set = (id: string, patch: Partial<Draft[string]>) =>
    setDraft((d) => ({ ...d, [id]: { ...(d[id] ?? { text: "", unknown: false }), ...patch } }));
  const toggle = (key: string) => setConfirmed((s) => {
    const next = new Set(s);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  return (
    <section className="max-w-[960px]">
      {/* fieldset disabled: locks every input at once for viewing members. */}
      <fieldset disabled={readOnly} className="m-0 min-w-0 border-0 p-0">
      <div className="bg-white border border-slate-200 rounded-xl px-[18px] py-3.5 mb-4 text-[13px]">
        <b className="text-slate-900">{t("handoff.supp.rules.title")}</b>
        <ul className="mt-1.5 pl-5 list-disc space-y-0.5 text-slate-600">
          <li>{t("handoff.supp.rule.onlyGaps")}</li>
          <li>{t("handoff.supp.rule.grade")}</li>
          <li>{t("handoff.supp.rule.noDefault")}</li>
          <li>{t("handoff.supp.rule.unknown")}</li>
          <li>{t("handoff.supp.rule.owner")}</li>
        </ul>
      </div>

      {total === 0 ? (
        <p className="mb-4 text-sm text-slate-500">
          {t("handoff.supp.none")}
          {!view.has_package && <span className="block mt-0.5 text-xs">{t("handoff.supp.open.noPackage")}</span>}
        </p>
      ) : (
        <p className="mb-3.5 text-[13px] text-slate-500">
          {t("handoff.supp.progress").replace("{done}", String(done)).replace("{total}", String(total))}
        </p>
      )}

      {groups.map(([section, questions]) => (
        <div key={section} className="mb-[18px]">
          <h4 className="mb-2 text-[15px] font-semibold text-slate-900">
            {sectionLabel(t, section as SupplementQuestion["section"])}
          </h4>
          {questions.map((q) => renderAnswer(q.id, t(`handoff.q.${q.id}` as keyof Dict),
                                              t(`handoff.q.${q.id}.hint` as keyof Dict)))}
        </div>
      ))}

      {view.open_questions.length > 0 && (
        <div className="mb-[18px]">
          <h4 className="text-[15px] font-semibold text-slate-900">
            {t("handoff.supp.open.title").replace("{n}", String(view.open_questions.length))}
          </h4>
          <p className="mb-2.5 text-xs text-slate-500">{t("handoff.supp.open.hint")}</p>
          {view.open_questions.map((q) => renderAnswer(q.key, openLabel(q),
            q.external ? t("handoff.supp.external.hint") : null, q.options, q.external))}
        </div>
      )}

      {view.open_deferred.length > 0 && (
        <details className="mb-[18px]">
          <summary className="cursor-pointer text-[13px] font-semibold text-slate-700">
            {t("handoff.supp.open.deferred").replace("{n}", String(view.open_deferred.length))}
          </summary>
          <p className="mt-1 mb-2.5 text-xs text-slate-500">{t("handoff.supp.open.deferredHint")}</p>
          {view.open_deferred.map((q) => renderAnswer(q.key, openLabel(q),
            q.until ? `${t("handoff.supp.open.until")}: ${q.until}` : null))}
        </details>
      )}

      {view.open_answered.length > 0 && (
        <details className="mb-[18px]">
          <summary className="cursor-pointer text-[13px] font-semibold text-slate-700">
            {t("handoff.supp.open.answered").replace("{n}", String(view.open_answered.length))}
          </summary>
          <p className="mt-1 mb-2.5 text-xs text-slate-500">{t("handoff.supp.open.answeredHint")}</p>
          {view.open_answered.map((q) => renderAnswer(q.key, openLabel(q), null))}
        </details>
      )}

      {view.confirmations.length > 0 && (
        <div className="mb-[18px]">
          <h4 className="text-[15px] font-semibold text-slate-900">
            {t("handoff.supp.confirm.title")}{" "}
            <span className="text-xs font-normal text-slate-500">{t("handoff.supp.confirm.optional")}</span>
          </h4>
          <p className="mb-2.5 text-xs text-slate-500">{t("handoff.supp.confirm.hint")}</p>
          {view.has_package ? (
            <>
              {scope.length > 0 && (
                <>
                  <h5 className="mt-1 text-[13px] font-semibold text-amber-800">
                    {t("handoff.supp.confirm.scope").replace("{n}", String(scope.length))}
                  </h5>
                  <p className="mb-1.5 text-xs text-amber-800">{t("handoff.supp.confirm.scopeHint")}</p>
                  <ul className="mb-3 bg-white border border-amber-300 rounded-xl">
                    {scope.map(renderItem)}
                  </ul>
                </>
              )}
              <h5 className="mt-1 mb-1.5 text-[13px] font-semibold text-slate-900">
                {t("handoff.supp.confirm.inVision").replace("{n}", String(inVision.length))}
              </h5>
              {inVision.length > 0 ? (
                <ul className="mb-3 bg-white border border-violet-200 rounded-xl">
                  {inVision.map(renderItem)}
                </ul>
              ) : (
                <p className="mb-3 text-xs text-slate-500">{t("handoff.supp.confirm.inVisionNone")}</p>
              )}
              {restCount > 0 && (
                <h5 className="mb-1.5 text-[13px] font-semibold text-slate-700">
                  {t("handoff.supp.confirm.rest").replace("{n}", String(restCount))}
                </h5>
              )}
            </>
          ) : (
            <p className="mb-2 text-xs text-violet-700">{t("handoff.supp.confirm.noPackage")}</p>
          )}
          {/* Collapsed by stage. Confirmation is not a to-do; it is where you pick the decisions you want to look into. */}
          {stages.map(([stage, items]) => (
            <details key={stage} className="mb-2 bg-white border border-slate-200 rounded-xl">
              <summary className="px-4 py-2.5 cursor-pointer text-[13px] font-semibold text-slate-900">
                {t(`handoff.stage.${stage}` as keyof Dict)}{" "}
                <span className="font-normal text-slate-500">
                  {t("handoff.supp.confirm.count").replace("{n}", String(items.length))
                    .replace("{done}", String(items.filter((c) => confirmed.has(c.key)).length))}
                </span>
              </summary>
              <ul>
                {items.map(renderItem)}
              </ul>
            </details>
          ))}
        </div>
      )}

      {view.superseded.length > 0 && (
        <div className="mb-[18px] text-[13px] text-slate-500">
          <b className="text-slate-700">{t("handoff.supp.superseded.title")}</b>
          <p className="text-xs">{t("handoff.supp.superseded.hint")}</p>
          <ul className="mt-1 pl-5 list-disc">
            {view.superseded.map((q) => (
              <li key={q.id}>{t(`handoff.q.${q.id}` as keyof Dict)}: {q.answer?.unknown ? t("handoff.supp.unknown") : q.answer?.text}</li>
            ))}
          </ul>
        </div>
      )}

      </fieldset>

      <div className="flex items-center gap-3 mt-2 pt-3.5 border-t border-slate-200">
        <button type="button" onClick={onBack} className="text-[13px] text-violet-700 hover:underline">
          {t("handoff.supp.back")}
        </button>
        <span className="flex-1" />
        {message && <span className="text-xs text-slate-500">{message}</span>}
        {!readOnly && (<>
        <button type="button" disabled={busy} onClick={() => onSave(toUpdate(view, draft, confirmed))}
                className="px-3 py-2 rounded-lg border border-slate-300 text-[13px] text-slate-600 disabled:opacity-45">
          {t("handoff.supp.save")}
        </button>
        <button type="button" disabled={busy} onClick={() => onSaveAndGenerate(toUpdate(view, draft, confirmed))}
                className="px-4 py-2 rounded-lg bg-violet-600 text-white text-[13px] font-bold hover:bg-violet-700 disabled:opacity-45">
          {t("handoff.supp.saveAndGenerate")}
        </button>
        </>)}
      </div>
      <p className="mt-2 text-xs text-slate-400">{t("handoff.supp.foot")}</p>
    </section>
  );
}
