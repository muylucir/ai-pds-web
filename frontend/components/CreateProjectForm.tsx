"use client";
import { useState } from "react";
import { createProject, ApiError } from "@/lib/api/client";
import type { ProjectSummary } from "@/lib/api/types";

export function CreateProjectForm({ onCreated }: { onCreated: (p: ProjectSummary) => void }) {
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createProject(projectId.trim(), name.trim() || undefined);
      onCreated(created);
      setProjectId("");
      setName("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("이미 존재하는 프로젝트 ID입니다.");
      } else if (err instanceof ApiError) {
        setError(`프로젝트 생성에 실패했습니다. (${err.status})`);
      } else {
        setError("네트워크 오류로 프로젝트를 생성하지 못했습니다.");
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
          프로젝트 ID
        </label>
        <input
          id="pid"
          required
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          placeholder="예: pilot2"
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <div className="flex-1">
        <label htmlFor="pname" className="block text-xs text-slate-500 mb-1">
          프로젝트 이름 (선택)
        </label>
        <input
          id="pname"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="예: 기획전 AI 어시스턴트"
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <button
        type="submit"
        disabled={submitting || projectId.trim() === ""}
        className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold"
      >
        프로젝트 생성
      </button>
      {error && <p className="text-sm text-rose-600 w-full sm:w-auto">{error}</p>}
    </form>
  );
}
