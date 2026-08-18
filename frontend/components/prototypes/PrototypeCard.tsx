// frontend/components/prototypes/PrototypeCard.tsx — one prototype's status +
// action buttons, following ArtifactCard's visual idiom (rounded-xl border,
// icon tile, violet primary / slate neutral) with StageTimeline's badge
// pattern (rounded-full pill, per-status color, pulsing while active).
//
// State machine mirrors Task 7's list_prototypes contract exactly:
//   none -> building -> built -> running
//                          \-------> failed
"use client";
import { useEffect, useRef, useState } from "react";

import type { PrototypeInfo, PrototypeState } from "@/lib/api/prototypes";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

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

// 라벨은 딕셔너리 키다 — 모듈 상수는 훅을 부를 수 없으므로 렌더에서 푼다.
const BADGE: Record<PrototypeState, { labelKey: keyof Dict; cls: string }> = {
  none: { labelKey: "proto.statusNone", cls: "bg-slate-100 text-slate-500" },
  building: { labelKey: "proto.statusBuilding", cls: "bg-violet-100 text-violet-700 animate-pulse" },
  built: { labelKey: "proto.statusBuilt", cls: "bg-emerald-50 text-emerald-700" },
  running: { labelKey: "proto.statusRunning", cls: "bg-sky-50 text-sky-700" },
  failed: { labelKey: "proto.statusFailed", cls: "bg-rose-50 text-rose-700" },
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
  shareUrl,
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
  /** 공유용 절대 URL(`absoluteShareUrl`이 서버의 `access_url`을 절대화한 값).
   *  주어지고 호스팅 중일 때만 복사 버튼이 뜬다 — 그 밖의 상태에서는 링크가
   *  502이므로 깨진 링크를 공유하게 된다(routes/proto_public.py).
   *
   *  이 URL에는 **접근 토큰이 들어 있다**. 참가자는 이것만으로 프로토타입에
   *  들어오므로, 공유 범위가 곧 접근 범위다. */
  shareUrl?: string;
  busy: boolean;
}) {
  const t = useT();
  const badge = BADGE[info.state];
  const label = t(badge.labelKey);
  const badgeLabel =
    info.state === "running" && info.port != null ? `${label} :${info.port}` : label;

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
            {t("proto.startBuild")}
          </button>
        )}
        {info.state === "building" && (
          <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
            {t("proto.openSession")}
          </button>
        )}
        {info.state === "built" && (
          <>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onStartHost}>
              {t("proto.startHosting")}
            </button>
            <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onBuild}>
              {t("proto.rebuild")}
            </button>
            {archiveUrl && <ArchiveLink href={archiveUrl} />}
          </>
        )}
        {info.state === "running" && (
          <>
            {onOpenPreview && (
              <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onOpenPreview}>
                {t("proto.openPreview")}
              </button>
            )}
            {shareUrl && <CopyLinkButton url={shareUrl} disabled={busy} />}
            <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onStopHost}>
              {t("proto.stopHosting")}
            </button>
            {onShowLogs && (
              <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onShowLogs}>
                {t("proto.logs")}
              </button>
            )}
            {archiveUrl && <ArchiveLink href={archiveUrl} />}
          </>
        )}
        {info.state === "failed" && (
          <>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
              {t("proto.rebuild")}
            </button>
            {onShowLogs && (
              <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onShowLogs}>
                {t("proto.logs")}
              </button>
            )}
          </>
        )}
        {info.state !== "none" && onReset && (
          <button
            type="button"
            aria-label={`${info.slug} ${t("proto.resetAria")}`}
            disabled={busy}
            onClick={() => onReset(info.slug)}
            className={DANGER_BTN}
          >
            {t("proto.reset")}
          </button>
        )}
        {/* 설문은 빌드 상태와 무관하게 항상 열 수 있다: 빌드 전 단계에서
            문항을 미리 만들 수도 있고, 호스팅이 끝난 뒤 응답을 집계할 수도
            있다. 빌드 드로어와 별개의 상태로 열리므로 서로 가리지 않는다. */}
        {onOpenSurvey && (
          <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onOpenSurvey}>
            {t("proto.survey")}
          </button>
        )}
      </div>
    </div>
  );
}

/** 프리뷰 링크를 클립보드로. 워크숍에서 참가자에게 링크를 나눠 주는 것이
 *  용도이므로 값은 **절대 URL**이어야 한다(호출부가 `absoluteShareUrl`로 만든다).
 *
 *  "복사됨"을 2초 후 되돌리는 이유: 두 번째 복사가 실제로 됐는지 화면에서
 *  구별되어야 한다. 라벨이 영구히 "복사됨"이면 눌렀는지 알 수 없다.
 *
 *  실패를 삼키지 않는다 — `navigator.clipboard`는 비-HTTPS 오리진이나 권한
 *  거부에서 없거나 던진다. 그때 "복사됨"을 띄우면 사용자가 빈 클립보드를
 *  붙여넣고 원인을 알 수 없다. */
function CopyLinkButton({ url, disabled }: { url: string; disabled: boolean }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 언마운트(호스팅 중지·리스트 갱신) 후 setState가 불리지 않게 정리한다.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      return;   // 성공한 척하지 않는다
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }

  return (
    <button type="button" className={SECONDARY_BTN} disabled={disabled}
            onClick={() => void copy()}>
      {copied ? t("proto.copied") : t("proto.copyLink")}
    </button>
  );
}

/** An <a>, not a button: the dev-team handoff is a plain file download, so
 *  the browser handles Content-Disposition and the filename (same shape as
 *  the survey CSV link). */
function ArchiveLink({ href }: { href: string }) {
  const t = useT();
  return (
    <a href={href} className={SECONDARY_BTN}>
      {t("proto.download")}
    </a>
  );
}
