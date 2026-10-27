"use client";
// frontend/components/DocChanges.tsx — a document, with what changed since the user's answers marked on it.
//
// **Why it exists (workshop feedback, 2026-10).** After a question round the agent revises the documents,
// and the user opens them again to check how the answers landed — and could not find what changed. The
// backend now keeps the documents as they stood at the moment answers were handed over (backend
// aipds/doc_revisions.py); this draws the comparison on the rendered document they already read.
//
// The default baseline is the newest one, i.e. the last time answers changed this document: that is the
// "what did my answers do" the feedback asked about. Older ones stay in the select — after several
// follow-up rounds the user may want everything since the first.
//
// With nothing to compare (no round yet, every project from before rounds existed, or the document is
// unchanged since) this renders exactly what `Markdown` does — no toolbar, nothing new to read.
import { useEffect, useMemo, useRef, useState } from "react";
import { listDocBaselines, readDocBaseline, type DocBaseline } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { changedSections, diffBlocks, type Change } from "@/lib/docDiff";
import { MARKDOWN_CLASSES, Markdown, MarkdownBlock } from "@/components/Markdown";
import { useT } from "@/lib/i18n/provider";
import { isDocPath } from "@/lib/useWorkspaceStream";

function stamp(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function useBaselineLabel() {
  const t = useT();
  return (b: DocBaseline) => {
    const name = b.source.slice(b.source.lastIndexOf("/") + 1);
    const key = b.kind === "revision" ? "docChanges.baselineRevision" : "docChanges.baselineAnswers";
    return `${stamp(b.at)} · ${t(key).replace("{name}", name)}`;
  };
}

// Scroll only the nearest scrolling ancestor. `scrollIntoView` also scrolls every ancestor that merely
// clips (overflow-hidden), and in the workspace that shoves the whole page sideways while the drawer is
// open — the same trap the drawer's focus handling documents (WorkspaceDocPanel, preventScroll).
function scrollToBlock(el: HTMLElement) {
  let box = el.parentElement;
  while (box) {
    const overflow = getComputedStyle(box).overflowY;
    if ((overflow === "auto" || overflow === "scroll") && box.scrollHeight > box.clientHeight) break;
    box = box.parentElement;
  }
  const offset = 8;
  if (box) {
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - offset;
    box.scrollTo({ top, behavior: "smooth" });
  } else {
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - offset, behavior: "smooth" });
  }
}

const ROW_MARK = "bg-amber-100";

function ChangeBlock({ change }: { change: Change }) {
  const t = useT();
  if (change.op === "same") return <MarkdownBlock text={change.block.text} />;
  if (change.op === "added") {
    return (
      <div data-change="added" className="my-2 rounded-r border-l-4 border-emerald-400 bg-emerald-50 pl-3 pr-2 py-1">
        <p className="not-prose m-0 text-[10px] font-bold text-emerald-700">{t("docChanges.added")}</p>
        <MarkdownBlock text={change.block.text} />
      </div>
    );
  }
  if (change.op === "removed") {
    return (
      <div data-change="removed" className="my-2 rounded-r border-l-4 border-rose-300 bg-rose-50 pl-3 pr-2 py-1">
        <p className="not-prose m-0 text-[10px] font-bold text-rose-700">{t("docChanges.removed")}</p>
        <div className="text-slate-500 line-through decoration-rose-400">
          <MarkdownBlock text={change.block.text} />
        </div>
      </div>
    );
  }
  return (
    <div data-change="modified" className="my-2 rounded-r border-l-4 border-amber-400 bg-amber-50 pl-3 pr-2 py-1">
      <p className="not-prose m-0 text-[10px] font-bold text-amber-700">{t("docChanges.modified")}</p>
      <MarkdownBlock text={change.block.text} markedRows={change.rows?.changed} rowClassName={ROW_MARK} />
      {change.rows !== undefined && change.rows.removed > 0 && (
        <p className="not-prose m-0 text-xs text-rose-700">
          {t("docChanges.rowsRemoved").replace("{n}", String(change.rows.removed))}
        </p>
      )}
      <details className="not-prose mt-1 text-xs">
        <summary className="cursor-pointer text-amber-800">{t("docChanges.before")}</summary>
        <div className={MARKDOWN_CLASSES + " mt-1 text-slate-500 line-through decoration-rose-400"}>
          <MarkdownBlock text={change.before.text} />
        </div>
      </details>
    </div>
  );
}

export function DocChanges({ projectId, path, text }: { projectId: string; path: string; text: string }) {
  const t = useT();
  const label = useBaselineLabel();
  // The current text is in the key: the backend leaves out baselines equal to it, so which ones exist
  // changes as the agent writes.
  //
  // Only documents have baselines (the same set the backend keeps, `is_document`): the review page opens
  // the audit log and question files in this viewer too, and asking for those would be refused.
  const comparable = isDocPath(path);
  const baselines = useAsync(
    () => (comparable ? listDocBaselines(projectId, path) : Promise.resolve([]))
      .then((list) => ({ path, list })),
    [projectId, path, text],
  );
  // `useAsync` keeps the previous result while a new one loads — tagged with its path so a document
  // never shows another document's comparison for that moment.
  const list = baselines.data?.path === path ? baselines.data.list : [];
  const [chosenId, setChosenId] = useState<string | null>(null);
  useEffect(() => setChosenId(null), [path]);
  const chosen = list.find((b) => b.id === chosenId) ?? list[0] ?? null;

  const baseline = useAsync(
    () => chosen === null || !chosen.existed
      ? Promise.resolve(null)
      : readDocBaseline(projectId, chosen.id, path).then((content) => ({ id: chosen.id, path, content })),
    [projectId, path, chosen?.id ?? ""],
  );
  const before = baseline.data && baseline.data.id === chosen?.id && baseline.data.path === path
    ? baseline.data.content : null;

  const [show, setShow] = useState(true);
  const changes = useMemo(() => (before === null ? null : diffBlocks(before, text)), [before, text]);
  const sections = useMemo(() => (changes === null ? [] : changedSections(changes)), [changes]);
  const counts = useMemo(() => {
    const c = { added: 0, modified: 0, removed: 0 };
    for (const ch of changes ?? []) if (ch.op !== "same") c[ch.op]++;
    return c;
  }, [changes]);
  const blockRefs = useRef<Array<HTMLDivElement | null>>([]);

  if (baselines.error !== null || baseline.error !== null) {
    return (
      <>
        <p className="text-xs text-slate-400 mb-2">{t("docChanges.loadFailed")}</p>
        <Markdown text={text} />
      </>
    );
  }
  if (chosen === null) return <Markdown text={text} />;

  const total = counts.added + counts.modified + counts.removed;
  return (
    <>
      <div className="mb-4 rounded-lg border border-violet-100 bg-violet-50/60 px-3 py-2 text-xs text-slate-600 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex items-center gap-1.5 font-bold text-violet-700 cursor-pointer">
            <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)}
                   className="accent-violet-600" />
            {t("docChanges.show")}
          </label>
          <span className="text-slate-400">·</span>
          <span>{t("docChanges.compareTo")}</span>
          {list.length > 1 ? (
            <select
              aria-label={t("docChanges.compareTo")}
              value={chosen.id}
              onChange={(e) => setChosenId(e.target.value)}
              className="min-w-0 max-w-full truncate bg-white border border-slate-200 rounded px-1.5 py-0.5"
            >
              {list.map((b) => <option key={b.id} value={b.id}>{label(b)}</option>)}
            </select>
          ) : (
            <span className="font-medium text-slate-700">{label(chosen)}</span>
          )}
          {show && changes !== null && total > 0 && (
            <span className="text-slate-500">
              ({t("docChanges.counts")
                .replace("{added}", String(counts.added))
                .replace("{modified}", String(counts.modified))
                .replace("{removed}", String(counts.removed))})
            </span>
          )}
        </div>
        {show && sections.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-bold text-slate-500">{t("docChanges.sections")}</span>
            {sections.map((s) => (
              <button
                key={s.first}
                type="button"
                onClick={() => {
                  const el = blockRefs.current[s.first];
                  if (el) scrollToBlock(el);
                }}
                className="max-w-[16rem] truncate rounded-full border border-violet-200 bg-white px-2 py-0.5 text-violet-700 hover:bg-violet-100"
              >
                {s.heading ?? t("docChanges.top")}
              </button>
            ))}
          </div>
        )}
        {show && !chosen.existed && (
          <p className="text-slate-600">{t("docChanges.newDoc").replace("{baseline}", label(chosen))}</p>
        )}
        {show && changes !== null && total === 0 && (
          <p className="text-slate-600">{t("docChanges.spacingOnly")}</p>
        )}
      </div>
      {show && changes !== null && total > 0 ? (
        <div className={MARKDOWN_CLASSES}>
          {changes.map((c, i) => (
            <div key={i} ref={(el) => { blockRefs.current[i] = el; }}>
              <ChangeBlock change={c} />
            </div>
          ))}
        </div>
      ) : (
        <Markdown text={text} />
      )}
    </>
  );
}
