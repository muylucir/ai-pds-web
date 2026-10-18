// frontend/components/prototypes/PrototypeCard.tsx — one prototype's status +
// action buttons (rounded-xl border, icon tile, violet primary / slate
// neutral) with StageTimeline's badge
// pattern (rounded-full pill, per-status color, pulsing while active).
//
// State machine mirrors Task 7's list_prototypes contract exactly:
//   none -> building -> built -> running
//                          \-------> failed
"use client";
import { useEffect, useRef, useState } from "react";

import type { HostState, PrototypeInfo, PrototypeState } from "@/lib/api/prototypes";
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

// Labels are dictionary keys — module constants cannot call hooks, so they are resolved at render.
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
  startingPhase,
  hostError,
  archiveUrl,
  shareUrl,
  busy,
  readOnly = false,
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
  /** The step while starting hosting is in progress. `POST /host` awaits all of npm install → build →
   *  port wait (routes/prototypes.py), so in the meantime the card had only disabled
   *  buttons and read as "nothing happening". The caller polls `GET /host` and
   *  passes it in — it is state the server already records. */
  startingPhase?: HostState | null;
  /** Why starting or stopping hosting was refused. Shown inside the card, right next to the buttons. */
  hostError?: string | null;
  archiveUrl?: string;
  /** Absolute URL for sharing (`absoluteShareUrl` made absolute from the server's `access_url`).
   *  The copy button appears only when this is given and the prototype is hosted — in any other state the link
   *  returns 502, so a broken link would be shared (routes/proto_public.py).
   *
   *  This URL **contains an access token**. Participants get into the prototype with this alone,
   *  so the sharing scope is the access scope. */
  shareUrl?: string;
  busy: boolean;
  /** Viewer member. Only preview, link, logs, download and viewing surveys remain; no build, hosting or reset. */
  readOnly?: boolean;
}) {
  const t = useT();
  const badge = BADGE[info.state];
  const label = t(badge.labelKey);
  const badgeLabel =
    info.state === "running" && info.port != null ? `${label} :${info.port}` : label;

  // While hosting is starting, we know **something more accurate** than the list's state (`built`).
  // While `POST /host` awaited all of npm install → build → port wait, the card was stuck at
  // "build complete + disabled buttons" — an observed 13-minute stretch that users
  // read as "nothing happening". The server already records that step.
  //
  // `failed`/`stopped` are not used here: by then the POST has already returned
  // (failure is 502) and the list is reread, so a lingering progress indicator would be stale information.
  const PHASE_KEYS: Partial<Record<HostState, keyof Dict>> = {
    installing: "proto.hostInstalling",
    building: "proto.hostBuildingHost",
    running: "proto.hostStarting",
  };
  const phaseKey = startingPhase ? PHASE_KEYS[startingPhase] : undefined;

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
          {/* The title is the name, and the slug only when there is none. The single-prototype layout's
              slug is the constant `"prototype"` (backend proto/layout.py), so this fallback
              produced the same title in every Path A.1 project. The slug itself remains
              as an identifier and still appears in the reset aria-label and the spec path below. */}
          <span className="font-medium text-sm truncate">{info.name ?? info.slug}</span>
          <span
            className={`text-[11px] px-2 py-0.5 rounded-full shrink-0 ${
              phaseKey
                ? "bg-violet-100 text-violet-700 animate-pulse"
                : badge.cls
            }`}
            // Also tell screen readers it is in progress — a change of text alone
            // is not read out.
            aria-live={phaseKey ? "polite" : undefined}
          >
            {phaseKey ? t(phaseKey) : badgeLabel}
          </span>
          {/* Survey status. **Attached only when `state !== "none"`** — at a stage with nothing
              to build yet, "no survey" reads like a to-do and becomes noise.

              `has_survey` is needed because `response_count` is the same in two states:
              0 with no survey, and 0 with a survey that has no responses yet. Observed in
              ws-n: only 1 of 3 prototypes had a survey, but the cards did not show
              that, so there was no way to notice the other two were missing. */}
          {info.state !== "none" && (
            <span
              className={`text-[11px] shrink-0 ${
                info.has_survey ? "text-slate-500" : "text-amber-600"
              }`}
            >
              {info.has_survey
                ? t("proto.surveyResponses").replace("{n}", String(info.response_count))
                : t("proto.surveyNone")}
            </span>
          )}
          {/* The stretch where the running server is older than the source. Links sent to participants stay
              alive, but what they show is the **previous version** — new code only takes effect after
              hosting again.

              **Judged by `preview_stale`, not `session_open`.** Keyed on the session, the warning would
              vanish the moment the session closes, yet that is exactly when the user is most likely to
              assume "it has been applied" (the server still serves the previous version).

              It **states the cost of the fix too**: hosting again waits for all of npm install →
              build → server start (observed up to 13 minutes), and participant links are closed
              during that time. The reason for putting it next to the button rather than adding another
              confirmation dialog is that this is where it gets read before pressing. */}
          {info.preview_stale && (
            <span className="text-[11px] shrink-0 text-amber-600">
              {t("proto.previewStale")} · {t("proto.rehostDowntime")}
            </span>
          )}
        </div>
        <span className="block text-[11px] text-slate-400 mt-0.5 truncate">{info.spec_path}</span>
        {hostError && (
          <p role="alert" className="text-xs text-rose-600 mt-1">{hostError}</p>
        )}
      </div>
      {/* flex-wrap: in `running` the buttons go up to eight: preview, link, stop, logs, download, modify, reset, survey.
          Unless the shrink-0 buttons wrap, they overflow the card on narrow
          screens. */}
      <div className="flex flex-wrap justify-end items-center gap-2 shrink-0">
        {info.state === "none" && !readOnly && (
          <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
            {t("proto.startBuild")}
          </button>
        )}
        {info.state === "building" && !readOnly && (
          <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
            {t("proto.openSession")}
          </button>
        )}
        {info.state === "built" && (
          <>
            {!readOnly && (
              <>
                <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onStartHost}>
                  {t("proto.startHosting")}
                </button>
                <BuildButton info={info} busy={busy} onBuild={onBuild} />
              </>
            )}
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
            {/* Shown only when stale. Always present, "다시 호스팅" (host again) would be a button whose effect
                is unclear (when already current it only closes the preview for a few minutes), while when stale
                it is, conversely, the only action needed. The warning line above states its cost. */}
            {info.preview_stale && !readOnly && (
              <button type="button" className={SECONDARY_BTN} disabled={busy}
                      onClick={onStartHost}>
                {t("proto.rehost")}
              </button>
            )}
            {/* Modifying is possible even while running. **Why it must be here**: this is the moment
                one wants to change something — right after seeing the preview. Previously
                this state had no build-related buttons at all, so hosting had to be stopped first
                for the modify button to come back. */}
            {!readOnly && (
              <>
                <BuildButton info={info} busy={busy} onBuild={onBuild} />
                <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onStopHost}>
                  {t("proto.stopHosting")}
                </button>
              </>
            )}
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
            {!readOnly && (
              <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onBuild}>
                {t("proto.continueBuild")}
              </button>
            )}
            {onShowLogs && (
              <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onShowLogs}>
                {t("proto.logs")}
              </button>
            )}
          </>
        )}
        {info.state !== "none" && onReset && !readOnly && (
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
        {/* The survey can always be opened regardless of build state: questions can be drafted
            before the build, and responses can be aggregated after hosting ends.
            It opens as a state separate from the build drawer, so they do not hide each other. */}
        {onOpenSurvey && (
          <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onOpenSurvey}>
            {t("proto.survey")}
          </button>
        )}
      </div>
    </div>
  );
}

/** The button that works on an already-built prototype. **One action, two names.**
 *
 *  If a session is already open, the job is to return to it, so it is "세션 열기" (open session).
 *  Otherwise it opens a new session, and what that session asks has already been split by the
 *  backend — "what to improve" for a completed build, "what to continue" for a build that died
 *  without completing (proto/session's handoff and resume branches). From the user's side both are
 *  modifications, so they go by one name.
 *
 *  Why `failed` does not use this component: what that state needs is not modifying but
 *  finishing ("이어서 하기", continue), and the button must be primary — that card has no other
 *  main action. */
function BuildButton({
  info, busy, onBuild,
}: { info: PrototypeInfo; busy: boolean; onBuild: () => void }) {
  const t = useT();
  return (
    <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onBuild}>
      {t(info.session_open ? "proto.openSession" : "proto.modify")}
    </button>
  );
}

/** Copies the preview link to the clipboard. Its purpose is handing out the link to participants in a workshop,
 *  so the value must be an **absolute URL** (the caller builds it with `absoluteShareUrl`).
 *
 *  Why "복사됨" (copied) reverts after 2 seconds: whether a second copy actually happened must be
 *  distinguishable on screen. If the label stayed "복사됨" permanently, you could not tell whether you pressed it.
 *
 *  Failures are not swallowed — `navigator.clipboard` is missing or throws on non-HTTPS origins or
 *  permission denial. Showing "복사됨" then would leave the user pasting an empty clipboard
 *  with no idea why. */
function CopyLinkButton({ url, disabled }: { url: string; disabled: boolean }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clean up so setState is not called after unmount (hosting stopped, list refreshed).
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      return;   // do not pretend it succeeded
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
