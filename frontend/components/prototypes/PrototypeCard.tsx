// frontend/components/prototypes/PrototypeCard.tsx — one prototype's status +
// action buttons, following ArtifactCard's visual idiom (rounded-xl border,
// icon tile, violet primary / slate neutral) with StageTimeline's badge
// pattern (rounded-full pill, per-status color, pulsing while active).
//
// State machine mirrors Task 7's list_prototypes contract exactly:
//   none -> building -> built -> running
//                          \-------> failed
"use client";
import type { PrototypeInfo, PrototypeState } from "@/lib/api/prototypes";

// Colour-free geometry, shared by every button below. Colour classes are
// each button's OWN set (never composed on top of another button's colour
// classes): Tailwind emits utility rules in a fixed internal order that
// ignores className string order, so appending e.g. `text-rose-600` after a
// constant that already carries `text-slate-700` does NOT override it — the
// slate rule wins the cascade regardless of which comes later in the string.
// Composing SECONDARY_BTN + rose overrides was tried and silently rendered
// as slate; this shape avoids ever having two same-property colour
// utilities on one element.
const BTN_BASE = "px-3.5 py-2 rounded-lg text-sm font-medium disabled:opacity-50";
const PRIMARY_BTN = `${BTN_BASE} bg-violet-600 hover:bg-violet-700 text-white disabled:hover:bg-violet-600`;
const SECONDARY_BTN = `${BTN_BASE} border border-slate-200 hover:bg-slate-50 text-slate-700 disabled:hover:bg-transparent`;
const DANGER_BTN = `${BTN_BASE} border border-slate-200 hover:bg-rose-50 text-rose-600 disabled:hover:bg-transparent`;

const BADGE: Record<PrototypeState, { label: string; cls: string }> = {
  none: { label: "스펙만 있음", cls: "bg-slate-100 text-slate-500" },
  building: { label: "빌드 중", cls: "bg-violet-100 text-violet-700 animate-pulse" },
  built: { label: "빌드 완료", cls: "bg-emerald-50 text-emerald-700" },
  running: { label: "실행 중", cls: "bg-sky-50 text-sky-700" },
  failed: { label: "실패", cls: "bg-rose-50 text-rose-700" },
};

export function PrototypeCard({
  info,
  onBuild,
  onOpenPreview,
  onStartHost,
  onStopHost,
  onShowLogs,
  onOpenSurvey,
  onReset,
  archiveUrl,
  busy,
}: {
  info: PrototypeInfo;
  onBuild: () => void;
  onOpenPreview?: () => void;
  onStartHost: () => void;
  onStopHost: () => void;
  onShowLogs?: () => void;
  onOpenSurvey?: () => void;
  /** Wipe build + session + survey, keeping the spec. Rendered only when
   *  `info.state !== "none"` — there is nothing accumulated to clear. */
  onReset?: (slug: string) => void;
  archiveUrl?: string;
  busy: boolean;
}) {
  const badge = BADGE[info.state];
  const badgeLabel =
    info.state === "running" && info.port != null ? `${badge.label} :${info.port}` : badge.label;

  return (
    <div className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 flex items-center gap-3">
      <span
        className="w-10 h-10 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center text-lg shrink-0"
        aria-hidden="true"
      >
        🧪
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm truncate">{info.slug}</span>
          <span className={`text-[11px] px-2 py-0.5 rounded-full shrink-0 ${badge.cls}`}>{badgeLabel}</span>
        </div>
        <span className="block text-[11px] text-slate-400 mt-0.5 truncate">{info.spec_path}</span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {info.state === "none" && (
          <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
            빌드 시작
          </button>
        )}
        {info.state === "building" && (
          <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
            세션 열기
          </button>
        )}
        {info.state === "built" && (
          <>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onStartHost}>
              호스팅 시작
            </button>
            <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onBuild}>
              다시 빌드
            </button>
            {archiveUrl && <ArchiveLink href={archiveUrl} />}
          </>
        )}
        {info.state === "running" && (
          <>
            {onOpenPreview && (
              <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onOpenPreview}>
                프리뷰 열기
              </button>
            )}
            <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onStopHost}>
              호스팅 중지
            </button>
            {onShowLogs && (
              <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onShowLogs}>
                로그
              </button>
            )}
            {archiveUrl && <ArchiveLink href={archiveUrl} />}
          </>
        )}
        {info.state === "failed" && (
          <>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
              다시 빌드
            </button>
            {onShowLogs && (
              <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onShowLogs}>
                로그
              </button>
            )}
          </>
        )}
        {info.state !== "none" && onReset && (
          <button
            type="button"
            aria-label={`${info.slug} 초기화`}
            disabled={busy}
            onClick={() => onReset(info.slug)}
            className={DANGER_BTN}
          >
            초기화
          </button>
        )}
        {/* 설문은 빌드 상태와 무관하게 항상 열 수 있다: 스펙만 있는 단계에서
            문항을 미리 만들 수도 있고, 호스팅이 끝난 뒤 응답을 집계할 수도
            있다. 빌드 드로어와 별개의 상태로 열리므로 서로 가리지 않는다. */}
        {onOpenSurvey && (
          <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onOpenSurvey}>
            설문
          </button>
        )}
      </div>
    </div>
  );
}

/** An <a>, not a button: the dev-team handoff is a plain file download, so
 *  the browser handles Content-Disposition and the filename (same shape as
 *  the survey CSV link). */
function ArchiveLink({ href }: { href: string }) {
  return (
    <a href={href} className={SECONDARY_BTN}>
      다운로드
    </a>
  );
}
