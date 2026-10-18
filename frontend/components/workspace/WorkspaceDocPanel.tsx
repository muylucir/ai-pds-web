"use client";
// frontend/components/workspace/WorkspaceDocPanel.tsx
"use client";
import Link from "next/link";
import { memo, useEffect, useId, useRef, useState } from "react";
import { listArtifacts, readArtifact, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { Markdown } from "@/components/Markdown";
import { useT } from "@/lib/i18n/provider";
import { DocIcon } from "@/components/workspace/OpenDocButton";

// The workspace's document drawer. Renders the document the CONVERSATION is
// currently about (activeDoc — tracks not only submit_document events but also
// doc-like file_changed, the ui-bug2 sync fix) so the user reads it without leaving
// the workspace.
//
// **It is an overlay drawer, not a column.** While it was always open as a 4th column, the
// question form was 412px wide at 1440px (AI-PDS team feedback, 2026-10). Now chat and
// questions split the width 5:5, and the document opens on top when needed. While it
// overlays, it covers the question form, so there are three ways to close it — the handle
// tab, ✕, and Esc / clicking outside.
//
// The handle tab is attached to the drawer's left edge and moves with it — when closed it
// sits at the screen's right edge (the margin the grid leaves empty), and when open, beside
// the drawer. Opening and closing are the same button in the same place. It shows text and
// a count rather than just an icon.
//
// turnSeq: a sequence that increments every time a turn ends. When a document event
// arrives, the VM→S3 sync may not have happened yet, so the read may be empty or 404;
// it is therefore re-read when the turn ends (included in the fetch key).
//
// It opens at every width — on narrow screens it covers the full width. Back when it was a
// column, it was hidden below `lg` and the review screen was the only alternative.
// Wait (ms) to batch files written in quick succession into a single list refetch.
const CHANGE_SETTLE_MS = 400;

// The value of `value` once it has not changed for `ms`. While it keeps changing, returns the last
// settled value.
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return settled;
}

// `memo`: during streaming the workspace page re-renders every frame (the chat bubble
// grows), but this panel's props stay the same throughout.
export const WorkspaceDocPanel = memo(function WorkspaceDocPanel({
  projectId,
  activeDoc,
  turnSeq,
  changedPaths,
  open,
  onOpenChange,
  onCurrentDocChange,
}: {
  projectId: string;
  activeDoc: { path: string; version: string | null } | null;
  turnSeq: number;
  // Paths written this turn (`file_changed`). **This is the trigger for refetching the list.**
  //
  // The previous key was `[projectId, turnSeq, activeDoc?.path]`, and there was no real-time
  // "a file changed" signal — `turnSeq` rises when a turn ends and `activeDoc` changes only
  // on document events. So in turns that write only audit/state/question files, the list
  // stayed at its mount-time value (an empty array for a new project) and only the
  // "최근 산출물" ("Recent artifacts") section on the right filled in — that is the shape
  // of the measured symptom where the dropdown never appeared at all.
  //
  // Why it is safe to key on this signal: the hook **publishes first, then advertises**
  // (publish_file → file_changed in claude_driver._on_post_tool_use). So by the time a path
  // lands in this array, that file is already in the source of truth and the refetch is sure to
  // find it.
  //
  // **It is not optional.** With a default of `[]`, it would compile even if the parent did
  // not pass it, and this refetch would silently die only in production — making it
  // required lets the type check guard the wiring.
  changedPaths: string[];
  // The parent holds the open state — the chat banner and the question form's "문서 열기"
  // ("Open document") open the same drawer too (workspace/page.tsx). The hooks below keep
  // running while closed: the current document must show the moment it opens, and this
  // component must also know whether a new document arrived while closed.
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Document path the drawer will show right now. Used by the question form's "{이름} 보기"
  // ("View {name}") button — only this component, which also knows the list fallback,
  // knows exactly what will open.
  onCurrentDocChange: (path: string | null) => void;
}) {
  const t = useT();
  // Dropdown selection state. null = follow activeDoc. When activeDoc changes (a new
  // document event), the manual selection resets to follow the conversation — the spec's priority.
  const [manualPath, setManualPath] = useState<string | null>(null);
  useEffect(() => {
    setManualPath(null);
  }, [activeDoc?.path]);
  const version = activeDoc?.version ?? null;

  // Artifact list — refetched when a turn ends (turnSeq) and **whenever a new document
  // appears** (activeDoc.path).
  //
  // Why `activeDoc?.path` is in the key (2026-08-18): the backend now publishes documents
  // to the source of truth right after writing them (backend/aipds/workspace_sync.py), so
  // the list can be accurate even mid-turn. Waiting only for the turn end means that while
  // a turn writes 5 documents, the dropdown shows only the current one (the union below),
  // and the earlier ones are missing from the list even though they are in the source of
  // truth — that is the shape of the measured symptom "appears briefly, then disappears".
  //
  // The list may not include the path yet (the short window where the event arrives before
  // publishing), so the current path is always unioned in — this also solves the
  // dropdown/body mismatch and the "select itself is hidden when the list is empty" problem.
  //
  // `changedPaths.length` is keyed **after a short wait** (useSettled). The agent writes
  // several files back to back, and refetching the list each time would cost as many
  // round trips as writes — a burst is batched into one. The other two are not batched: the
  // turn end happens once per turn, and for a new document the body and dropdown must follow
  // immediately.
  const settledChanges = useSettled(changedPaths.length, CHANGE_SETTLE_MS);
  const artifacts = useAsync(() => listArtifacts(projectId),
                             [projectId, turnSeq, activeDoc?.path ?? "",
                              settledChanges]);
  const listed = artifacts.data ?? [];
  // **Fallback to the list.** Previously it was `manualPath ?? activeDoc?.path ?? null`, so
  // with no `activeDoc` the body was empty — the dropdown is filled from `listed`, so "the
  // options are full but the body says '아직 문서가 없습니다' (no documents yet)" was
  // structurally possible, and after a refresh `activeDoc` is always null, so it was
  // always in that state.
  //
  // Why picking the first item is meaningful: the backend returns newest first
  // (Workspace.list_artifacts). While it was alphabetical, the first item was almost
  // always `aiplc-docs/audit.md`.
  const path = manualPath ?? activeDoc?.path ?? listed[0] ?? null;
  const options = path && !listed.includes(path) ? [...listed, path] : listed;

  // 404 → held separately as "not synced yet". Mid-turn this is normal (the event arrives
  // before the sync), but if it is still 404 after the turn ends, that document is not in S3 —
  // previously this rendered as an empty document and looked like "it says it was created but
  // there is no content".
  const MISSING = Symbol.for("doc-missing");
  const content = useAsync(
    () =>
      path === null
        ? Promise.resolve("")
        : readArtifact(projectId, path).catch((e) =>
            e instanceof ApiError && e.status === 404
              ? (MISSING as unknown as string)
              : Promise.reject(e),
          ),
    // turnSeq in the key: re-read at every turn end (compensates for mid-turn sync lag).
    [projectId, path, turnSeq],
  );
  const missing = content.data === (MISSING as unknown as string);
  const text = missing ? "" : (content.data ?? "");

  const loadError = path !== null && content.error !== null;
  // Version strings from the backend may already carry a "v" prefix (e.g.
  // "v2") or not (e.g. "2"); normalize to a single leading "v".
  const versionLabel = version ? (/^v/i.test(version) ? version : `v${version}`) : null;

  useEffect(() => {
    onCurrentDocChange(path);
  }, [path, onCurrentDocChange]);

  // If the conversation writes a new document (or a new version) while closed, the tab
  // switches to "새 문서" ("New document") and bobs once. **It does not open on its own** —
  // an overlay drawer opening by itself would cover the question form being answered (the
  // same complaint as question2.png, where the question panel moved to the prototype on
  // its own).
  // It also does not keep blinking, for the same reason.
  const docKey = activeDoc ? `${activeDoc.path}@${activeDoc.version ?? ""}` : null;
  const [seenKey, setSeenKey] = useState(docKey);
  useEffect(() => {
    if (open) setSeenKey(docKey);
  }, [open, docKey]);
  const unseen = !open && docKey !== null && docKey !== seenKey;

  // On open, move focus into the drawer (✕); on close, return it to the handle. Esc closes it.
  //
  // **preventScroll is required.** At the moment of opening, ✕ is still sliding in from
  // off-screen right, and a plain focus() makes the browser scroll an ancestor to reveal it —
  // overflow-hidden does not prevent programmatic scrolling either. Measured (1440px): the
  // page root's scrollLeft jumped to 648 and returned to 0 as the drawer came in, so the
  // whole screen looked as if it was shoved left and snapped back. The grid's overflow-clip
  // (workspace/page.tsx) adds one more layer of protection against the same class of issue.
  const drawerId = useId();
  const tabRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(open);
  useEffect(() => {
    if (open && !wasOpen.current) closeRef.current?.focus({ preventScroll: true });
    if (!open && wasOpen.current) tabRef.current?.focus({ preventScroll: true });
    wasOpen.current = open;
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  return (
    <>
      {open && (
        <div
          aria-hidden="true"
          data-testid="doc-drawer-backdrop"
          className="absolute inset-0 z-20 bg-slate-900/10"
          onClick={() => onOpenChange(false)}
        />
      )}
      <div
        className={`absolute inset-y-0 right-0 z-30 w-full sm:w-[min(36rem,90vw)] lg:w-[45%] transition-transform duration-200 ${open ? "translate-x-0" : "translate-x-full"}`}
      >
        <button
          ref={tabRef}
          type="button"
          aria-expanded={open}
          aria-controls={drawerId}
          aria-label={open ? t("ws.closeDocAria") : t("ws.openDocAria")}
          onClick={() => onOpenChange(!open)}
          className={`absolute top-6 left-0 -translate-x-full w-9 py-3 flex flex-col items-center gap-2 rounded-l-xl border border-r-0 shadow-md text-xs font-bold ${
            unseen
              ? "bg-violet-600 border-violet-600 text-white"
              : "bg-white border-violet-200 text-violet-700 hover:bg-violet-50"
          }`}
        >
          {unseen && (
            // key: remount for each new document so it bobs once more — after that it stays still.
            <span key={docKey} className="relative flex w-2 h-2" aria-hidden="true">
              <span className="absolute inset-0 rounded-full bg-white motion-safe:animate-nudge" />
              <span className="relative w-2 h-2 rounded-full bg-white" />
            </span>
          )}
          {open ? <span aria-hidden="true">›</span> : <DocIcon className="w-4 h-4" />}
          <span className="[writing-mode:vertical-rl] tracking-wide" aria-hidden="true">
            {unseen ? t("ws.docTabNew") : t("ws.generatedDocs")}
          </span>
          {options.length > 0 && (
            <span
              aria-hidden="true"
              className={`min-w-5 px-1 rounded-full text-[10px] ${unseen ? "bg-white text-violet-700" : "bg-violet-100 text-violet-700"}`}
            >
              {options.length}
            </span>
          )}
        </button>
        <aside
          id={drawerId}
          aria-label={t("ws.generatedDocsAria")}
          aria-hidden={!open}
          inert={!open}
          className="h-full flex flex-col min-w-0 min-h-0 bg-white border-l border-slate-200 shadow-2xl"
        >
          <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-2">
            {options.length > 0 ? (
              <select
                aria-label={t("ws.selectDocAria")}
                value={path ?? ""}
                onChange={(e) => setManualPath(e.target.value)}
                className="min-w-0 flex-1 text-xs font-bold text-slate-600 bg-transparent border border-slate-200 rounded-lg px-2 py-1.5 truncate focus:outline-none focus:ring-2 focus:ring-violet-300"
              >
                {options.map((p) => (
                  <option key={p} value={p}>
                    {p.slice(p.lastIndexOf("/") + 1)}
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-xs font-bold text-slate-400 uppercase tracking-wide truncate">{t("ws.generatedDocs")}</p>
            )}
            {path !== null && (
              <button
                type="button"
                aria-label={t("ws.refreshDocAria")}
                disabled={content.loading}
                onClick={() => {
                  // Refetch both the current document and the artifact list — refreshing only
                  // the document would leave other just-created documents out of the dropdown.
                  content.reload();
                  artifacts.reload();
                }}
                className="shrink-0 w-7 h-7 rounded-lg text-slate-400 hover:text-violet-600 hover:bg-violet-50 disabled:opacity-40 flex items-center justify-center"
              >
                ↻
              </button>
            )}
            {versionLabel && manualPath === null && (
              <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">
                {versionLabel}
              </span>
            )}
            <button
              ref={closeRef}
              type="button"
              aria-label={t("ws.closeDocAria")}
              onClick={() => onOpenChange(false)}
              className="shrink-0 w-7 h-7 rounded-lg text-slate-400 hover:text-violet-600 hover:bg-violet-50 flex items-center justify-center"
            >
              ✕
            </button>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto p-4 text-sm text-slate-700">
            {/* Do not flatten a list failure into "no documents" — if the screen looks the same,
                the
                cause can never be seen (the same discipline by which this file separates docUnsaved
                from docEmpty). If a document is selected, its body takes priority: if there is a
                readable document even though the list failed, there is no reason to block it. */}
            {path === null && artifacts.error !== null ? (
              <p className="text-rose-600">{t("ws.docListFailed")}</p>
            ) : path === null ? (
              <p className="text-slate-400">
                {t("ws.noDocsYet")}
              </p>
            ) : loadError ? (
              <p className="text-rose-600">{t("ws.docLoadFailed")}</p>
            ) : content.loading ? (
              <p className="text-slate-400">{t("ws.docLoading")}</p>
            ) : missing ? (
              // Not saved at all — reported distinctly from "empty". If this were flattened
              // into an empty document, the user would believe the document was created, and
              // after a refresh could not tell why it vanished from the list.
              <p className="text-amber-700">{t("ws.docUnsaved")}</p>
            ) : text.trim() === "" ? (
              <p className="text-slate-400">{t("ws.docEmpty")}</p>
            ) : (
              <Markdown text={text} />
            )}
          </div>
          {path !== null && (
            <div className="p-3 border-t border-slate-100">
              <Link
                href={`/projects/${projectId}/review`}
                className="text-xs font-medium text-violet-700 underline hover:text-violet-900"
              >
                {t("ws.toFullReview")}
              </Link>
            </div>
          )}
        </aside>
      </div>
    </>
  );
});
