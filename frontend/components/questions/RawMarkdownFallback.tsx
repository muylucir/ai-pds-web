"use client";
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { Markdown } from "@/components/Markdown";
import { useT } from "@/lib/i18n/provider";

export function RawMarkdownFallback({
  file,
  onSubmit,
  submitting,
}: {
  file: QuestionFile;
  onSubmit: (text: string) => void;
  submitting: boolean;
}) {
  const t = useT();
  const [text, setText] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(text);
      }}
      className="space-y-4"
    >
      <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 px-5 py-4 text-sm">
        <p className="font-bold text-amber-900">{t("q.parseFailed")}</p>
        <p className="text-amber-800 mt-1">{t("q.parseFailedBody")}</p>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <Markdown text={file.raw_markdown ?? ""} />
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <label htmlFor="freeform" className="block text-sm font-medium mb-2">
          {t("q.freeformLabel")}
        </label>
        <textarea
          id="freeform"
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
        <div className="mt-3 flex justify-end">
          <button type="submit" disabled={submitting || text.trim() === ""} className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold">
            {t("q.submit")}
          </button>
        </div>
      </div>
    </form>
  );
}
