"use client";
import { useEffect, useState } from "react";
import { createProject, ApiError } from "@/lib/api/client";
import { listModels, type ModelOption } from "@/lib/api/models";
import type { ProjectSummary } from "@/lib/api/types";
import { DEFAULT_LOCALE, type Locale } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import { modelLabel } from "@/lib/modelLabel";

// Characters not allowed in a project id. The id is an S3 key prefix and the local workspace
// directory name, and it also goes into URL path segments, so values like spaces, slashes, or
// Korean characters would blow up later with encoding and path problems. Filter them at input.
const ID_DISALLOWED = /[^A-Za-z0-9_-]/g;

export function CreateProjectForm({ onCreated }: { onCreated: (p: ProjectSummary) => void }) {
  const t = useT();
  const [projectId, setProjectId] = useState("");
  // Whether characters were filtered out of the input. If they were just silently removed,
  // users would think their keyboard dropped keystrokes — the rule is shown only when
  // something was filtered. Once on, it stays until the input is cleared or creation
  // succeeds: clearing it as soon as the next character is valid would make the notice flash
  // by for one character, unreadable.
  const [idRejected, setIdRejected] = useState(false);
  const [name, setName] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelId, setModelId] = useState("");
  // Output language. Starts at ko regardless of the UI locale — this value is the language of
  // the documents, prototypes, and chat, and it is normal for someone using the English UI to
  // create a Korean project. Using the UI locale as the default would silently make that
  // choice for them.
  const [language, setLanguage] = useState<Locale>(DEFAULT_LOCALE);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The model list is a short list of at most 5, so it is fetched once on mount.
  // Failures pass harmlessly: the select becomes disabled and the server falls back to the env
  // default — a catalog lookup failure blocking project creation entirely would be excessive.
  useEffect(() => {
    let alive = true;
    void listModels()
      .then((list) => {
        if (!alive) return;
        setModels(list);
        setModelId(list[0]?.model_id ?? "");
      })
      .catch(() => {
        if (alive) setModels([]);
      });
    return () => { alive = false; };
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createProject(projectId.trim(), name.trim() || undefined,
                                          modelId || undefined, language);
      onCreated(created);
      setProjectId("");
      setIdRejected(false);
      setName("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(t("project.idExists"));
      } else if (err instanceof ApiError) {
        setError(`${t("project.createFailed")} (${err.status})`);
      } else {
        setError(t("project.createNetworkError"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white rounded-xl border border-slate-200 p-5 mb-8 flex flex-col sm:flex-row sm:items-end gap-3"
    >
      <div className="flex-1">
        <label htmlFor="pid" className="block text-xs text-slate-500 mb-1">
          {t("project.id")}
        </label>
        {/* Paste and autocomplete also go through onChange, so filtering once here covers
            every path. */}
        <input
          id="pid"
          required
          value={projectId}
          title={t("project.idCharsHint")}
          onChange={(e) => {
            const raw = e.target.value;
            const clean = raw.replace(ID_DISALLOWED, "");
            setProjectId(clean);
            if (clean !== raw) setIdRejected(true);
            else if (clean === "") setIdRejected(false);
          }}
          placeholder={t("project.idPlaceholder")}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <div className="flex-1">
        <label htmlFor="pname" className="block text-xs text-slate-500 mb-1">
          {t("project.nameOptional")}
        </label>
        <input
          id="pname"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("project.namePlaceholder")}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <div className="sm:w-56">
        <label htmlFor="pmodel" className="block text-xs text-slate-500 mb-1">
          {t("header.modelBadgeTitleShort")}
        </label>
        {/* Shows the name and effort — the model id goes only as the value. effort is the value the
            catalog decided, and on creation the server copies the same value into the project. */}
        <select
          id="pmodel"
          value={modelId}
          disabled={models.length === 0}
          onChange={(e) => setModelId(e.target.value)}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 bg-white disabled:bg-slate-50 disabled:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-400"
        >
          {models.length === 0 && <option value="">{t("project.defaultModel")}</option>}
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>{modelLabel(m.name, m.effort, t)}</option>
          ))}
        </select>
      </div>
      <div className="sm:w-36">
        <label htmlFor="plang" className="block text-xs text-slate-500 mb-1">
          {t("project.language")}
        </label>
        {/* Cannot be changed after creation — changing it midway would leave the documents and
            transcripts already created in the previous language, mixing within one project. */}
        <select
          id="plang"
          value={language}
          onChange={(e) => setLanguage(e.target.value as Locale)}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-violet-400"
        >
          <option value="ko">한국어</option>
          <option value="en">English</option>
        </select>
      </div>
      <button
        type="submit"
        disabled={submitting || projectId.trim() === ""}
        className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold"
      >
        {t("project.create")}
      </button>
      {/* An API error takes precedence — it matters more than the filtered-characters notice. */}
      {(error ?? (idRejected ? t("project.idCharsHint") : null)) && (
        <p className="text-sm text-rose-600 w-full sm:w-auto">
          {error ?? t("project.idCharsHint")}
        </p>
      )}
    </form>
  );
}
