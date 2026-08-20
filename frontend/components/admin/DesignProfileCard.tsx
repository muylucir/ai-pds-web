"use client";
import { DESIGN_RAW_PATH, type DesignProfile } from "@/lib/api/design";
import { useT } from "@/lib/i18n/provider";

//: 견본을 그릴지 판단하는 데만 쓴다(검증은 백엔드 파서 한 곳이다).
//  UploadDesignModal의 확인 단계가 같은 판단을 해야 하므로 내보낸다 — 두 벌이면
//  같은 값이 한쪽에서만 색으로 보인다.
export const COLOUR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

export function DesignProfileCard(
  { profile, onReplace, onRemove }:
  { profile: DesignProfile; onReplace: () => void; onRemove: () => void },
) {
  const t = useT();
  const entries = Object.entries(profile.tokens);
  return (
    <section className="rounded-xl border border-slate-200 p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium">{profile.filename}</p>
          <p className="mt-1 text-xs text-slate-500">
            {t("admin.designUploadedBy")}: {profile.uploaded_by} ·{" "}
            {profile.uploaded_at.slice(0, 10)}
          </p>
        </div>
        <div className="flex gap-2">
          <a href={DESIGN_RAW_PATH}
             className="rounded-lg border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50">
            {t("admin.designDownloadRaw")}
          </a>
          <button type="button" onClick={onReplace}
                  className="rounded-lg bg-violet-600 px-3 py-2 text-sm text-white hover:bg-violet-700">
            {t("admin.designReplace")}
          </button>
          <button type="button" onClick={onRemove}
                  className="rounded-lg border border-rose-200 px-3 py-2 text-sm text-rose-700 hover:bg-rose-50">
            {t("admin.designRemove")}
          </button>
        </div>
      </div>

      <h2 className="mt-5 text-sm font-medium">{t("admin.designTokens")}</h2>
      {/* 토큰이 없으면 색·서체는 화면에 닿지 않는다. 백엔드가 저장물에서 유도해
          내려주므로(routes/design.py의 _warnings) 업로드 직후와 다시 열었을 때가
          같은 말을 한다 — 조용히 지나가던 상태가 이 줄로 보인다. */}
      {profile.warnings?.includes("no-tokens") && (
        <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {t("admin.designNoTokens")}
        </p>
      )}
      {entries.length === 0 && !profile.warnings?.includes("no-tokens") && (
        <p className="mt-2 text-sm text-slate-500">—</p>
      )}
      <ul className="mt-2 flex flex-wrap gap-3">
        {entries.map(([key, value]) => (
          <li key={key} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
            {COLOUR_RE.test(value) && (
              <span aria-hidden className="size-5 rounded border border-slate-200"
                    style={{ backgroundColor: value }} />
            )}
            <span className="text-xs text-slate-500">{key}</span>
            <span className="text-xs font-mono">{value}</span>
          </li>
        ))}
      </ul>

      {profile.prose.trim() && (
        <details className="mt-5">
          <summary className="cursor-pointer text-sm font-medium">
            {t("admin.designProse")}
          </summary>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">
            {profile.prose}
          </pre>
        </details>
      )}
    </section>
  );
}
