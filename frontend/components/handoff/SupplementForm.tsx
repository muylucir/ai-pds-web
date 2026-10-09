// 보완 질문 — 재료가 없는 섹션만 묻는다(서버 handoff/supplement 헤더의 조건).
//
// 문항에 AI 기본값을 채워 두지 않는다. 실측에서 표시된 기본값을 PM이 고른 비율이 89%였다 —
// 기본값을 주면 이 답도 다시 "AI 제안 수락"이 된다. 빈 칸과 "모름"만 있다.
//
// 확인은 내용을 바꾸지 않는다. 바꾸는 길은 워크스페이스뿐이다 — 여기서 고치게 하면 같은
// 결정이 Discovery 산출물과 보완 답 두 곳에 다르게 남는다.
"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { STAGE_ORDER } from "@/lib/api/handoff";
import type {
  Confirmation, SupplementQuestion, SupplementQuestionId, SupplementUpdate, SupplementView,
} from "@/lib/api/handoff";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import { sectionLabel } from "./ReadinessPanel";

type Draft = Record<string, { text: string; unknown: boolean }>;

function initialDraft(view: SupplementView): Draft {
  const out: Draft = {};
  for (const q of view.questions) {
    out[q.id] = { text: q.answer?.text ?? "", unknown: q.answer?.unknown ?? false };
  }
  return out;
}

export function toUpdate(view: SupplementView, draft: Draft, confirmed: Set<string>): SupplementUpdate {
  const answers: SupplementUpdate["answers"] = {};
  for (const q of view.questions) {
    const d = draft[q.id];
    if (d && (d.unknown || d.text.trim())) answers[q.id as SupplementQuestionId] = d;
  }
  // 이미 대체된 답도 함께 보낸다. 저장은 교체라서, 빠뜨리면 기록에서 지워진다.
  for (const q of view.superseded) {
    if (q.answer) answers[q.id] = { text: q.answer.text, unknown: q.answer.unknown };
  }
  return { answers, confirmed: [...confirmed] };
}

export function SupplementForm({
  view, workspaceHref, busy, message, onSave, onSaveAndGenerate, onBack,
}: {
  view: SupplementView;
  workspaceHref: string;
  busy: boolean;
  message: string | null;
  onSave: (update: SupplementUpdate) => void;
  onSaveAndGenerate: (update: SupplementUpdate) => void;
  onBack: () => void;
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

  // 진행률은 빈칸 질문만 센다. 확인은 선택 사항이다 — 분모에 넣으면 질문을 다 채워도 "아직 40개
  // 남음"으로 보였다(실측 industry-safe-law).
  const total = view.questions.length;
  const done = view.questions.filter((q) => draft[q.id]?.unknown || draft[q.id]?.text.trim()).length;

  const stages = useMemo(() => {
    const byStage = new Map<string, Confirmation[]>();
    for (const c of view.confirmations) byStage.set(c.stage, [...(byStage.get(c.stage) ?? []), c]);
    return STAGE_ORDER.filter((st) => byStage.has(st)).map((st) => [st, byStage.get(st)!] as const);
  }, [view.confirmations]);

  const set = (id: string, patch: Partial<Draft[string]>) =>
    setDraft((d) => ({ ...d, [id]: { ...(d[id] ?? { text: "", unknown: false }), ...patch } }));
  const toggle = (key: string) => setConfirmed((s) => {
    const next = new Set(s);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  return (
    <section className="max-w-[960px]">
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
        <p className="text-sm text-slate-500">{t("handoff.supp.none")}</p>
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
          {questions.map((q) => {
            const d = draft[q.id] ?? { text: "", unknown: false };
            return (
              <div key={q.id} className={`bg-white border rounded-xl px-4 py-3.5 mb-2.5 ${
                d.unknown ? "border-dashed border-slate-300" : d.text.trim() ? "border-sky-200" : "border-slate-200"}`}>
                <label htmlFor={`q-${q.id}`} className="block text-sm font-semibold text-slate-900">
                  {t(`handoff.q.${q.id}` as keyof Dict)}
                </label>
                <p className="mb-2 text-xs text-slate-400">{t(`handoff.q.${q.id}.hint` as keyof Dict)}</p>
                <textarea id={`q-${q.id}`} value={d.unknown ? "" : d.text} disabled={d.unknown}
                          maxLength={4000} rows={3}
                          onChange={(e) => set(q.id, { text: e.target.value })}
                          className="w-full rounded-lg border border-slate-300 px-2.5 py-2 text-[13px] disabled:bg-slate-50" />
                <label className="mt-2 inline-flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
                  <input type="checkbox" checked={d.unknown}
                         onChange={(e) => set(q.id, { unknown: e.target.checked })} />
                  {t("handoff.supp.unknown")}
                </label>
              </div>
            );
          })}
        </div>
      ))}

      {view.confirmations.length > 0 && (
        <div className="mb-[18px]">
          <h4 className="text-[15px] font-semibold text-slate-900">
            {t("handoff.supp.confirm.title")}{" "}
            <span className="text-xs font-normal text-slate-500">{t("handoff.supp.confirm.optional")}</span>
          </h4>
          <p className="mb-2.5 text-xs text-slate-500">{t("handoff.supp.confirm.hint")}</p>
          {/* 단계별로 접어 둔다. 확인은 할 일이 아니라, 들여다보고 싶은 결정을 고르는 곳이다. */}
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
                {items.map((c) => {
                  const on = confirmed.has(c.key);
                  return (
                    <li key={c.key} className="px-4 py-3 border-t border-slate-100">
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
                      {c.note && (
                        <p className="mt-0.5 text-[11px] text-slate-400">
                          {t("handoff.supp.confirm.note")}: {c.note}
                        </p>
                      )}
                      <div className="mt-2 flex gap-1.5">
                        <button type="button" aria-pressed={on} onClick={() => toggle(c.key)}
                                className={`px-2.5 py-1 rounded-lg border text-xs ${
                                  on ? "bg-sky-50 border-sky-200 text-sky-700 font-bold" : "border-slate-300 text-slate-600"}`}>
                          {on ? t("handoff.supp.confirm.confirmed") : t("handoff.supp.confirm.yes")}
                        </button>
                        <Link href={workspaceHref} className="px-2.5 py-1 rounded-lg border border-slate-300 text-xs text-slate-600">
                          {t("handoff.supp.confirm.revise")}
                        </Link>
                      </div>
                    </li>
                  );
                })}
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

      <div className="flex items-center gap-3 mt-2 pt-3.5 border-t border-slate-200">
        <button type="button" onClick={onBack} className="text-[13px] text-violet-700 hover:underline">
          {t("handoff.supp.back")}
        </button>
        <span className="flex-1" />
        {message && <span className="text-xs text-slate-500">{message}</span>}
        <button type="button" disabled={busy} onClick={() => onSave(toUpdate(view, draft, confirmed))}
                className="px-3 py-2 rounded-lg border border-slate-300 text-[13px] text-slate-600 disabled:opacity-45">
          {t("handoff.supp.save")}
        </button>
        <button type="button" disabled={busy} onClick={() => onSaveAndGenerate(toUpdate(view, draft, confirmed))}
                className="px-4 py-2 rounded-lg bg-violet-600 text-white text-[13px] font-bold hover:bg-violet-700 disabled:opacity-45">
          {t("handoff.supp.saveAndGenerate")}
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-400">{t("handoff.supp.foot")}</p>
    </section>
  );
}
