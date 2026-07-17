import type { AuditEntry } from "@/lib/api/types";

// Renders backend audit data — NOT hardcoded mockup copy. "AI 검증 요약" shows
// the most recent AI responses as check lines; "승인 게이트 이력" shows entries
// whose context/response reference a gate or approval. The frontend applies no
// methodology judgment; it just surfaces what audit.md recorded.
export function VerificationSummary({ entries }: { entries: AuditEntry[] }) {
  const recent = [...entries].sort((a, b) => b.index - a.index);
  const gateHistory = recent.filter((e) =>
    /gate|approv|승인|게이트/i.test(`${e.context ?? ""} ${e.ai_response}`),
  );

  return (
    <div className="space-y-6">
      <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="check-heading">
        <div className="px-5 py-4 border-b border-slate-100">
          <h2 id="check-heading" className="font-bold">AI 검증 요약</h2>
        </div>
        <ul className="p-5 space-y-3 text-sm">
          {recent.slice(0, 5).map((e) => (
            <li key={e.index} className="flex gap-2.5">
              <span className="text-emerald-500" aria-hidden="true">✓</span>
              <span className="line-clamp-2">{e.ai_response}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="gate-heading">
        <div className="px-5 py-4 border-b border-slate-100">
          <h2 id="gate-heading" className="font-bold">승인 게이트 이력</h2>
        </div>
        <ul className="p-5 space-y-4 text-sm">
          {gateHistory.length === 0 && <li className="text-slate-400">기록된 승인 이력이 없습니다.</li>}
          {gateHistory.map((e) => (
            <li key={e.index} className="flex gap-3">
              <span className="shrink-0 w-6 h-6 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center text-xs" aria-hidden="true">
                ✓
              </span>
              <div>
                <p className="font-medium line-clamp-2">{e.ai_response}</p>
                <p className="text-xs text-slate-400">Entry {e.index}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="bg-slate-100 rounded-xl p-5 text-xs text-slate-500 leading-relaxed">
        <p className="font-medium text-slate-600 mb-1">🔒 감사 추적 (audit.md)</p>
        <p>
          모든 입력은 원문 그대로 타임스탬프와 함께 기록됩니다. API 키·크리덴셜은 절대 기록되지 않습니다.
          이 게이트에서의 승인/수정요청 결정도 즉시 기록됩니다.
        </p>
      </section>
    </div>
  );
}
