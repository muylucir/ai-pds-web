import { MarkdownView } from "./MarkdownView";

export function DocumentPanel({ markdown }: { markdown: string }) {
  return (
    <article className="lg:col-span-2 bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h2 className="font-bold">📕 discovery-document.md</h2>
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">Living Document</span>
        </div>
        <button className="px-2.5 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 text-slate-600 text-xs">
          .md 내보내기
        </button>
      </div>
      <div className="p-6 text-sm text-slate-700">
        {markdown.trim() === "" ? (
          <p className="text-slate-400">아직 작성된 문서가 없습니다.</p>
        ) : (
          <MarkdownView markdown={markdown} />
        )}
      </div>
    </article>
  );
}
