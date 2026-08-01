"use client";
import { useEffect, useState } from "react";
import { createProject, ApiError } from "@/lib/api/client";
import { listModels, type ModelOption } from "@/lib/api/models";
import type { ProjectSummary } from "@/lib/api/types";

export function CreateProjectForm({ onCreated }: { onCreated: (p: ProjectSummary) => void }) {
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelId, setModelId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 모델 목록은 최대 5개짜리 짧은 목록이라 마운트 시 한 번 받아 온다.
  // 실패는 무해하게 흘린다: 셀렉트가 비활성이 되고 서버가 env 기본값으로
  // 떨어진다 — 카탈로그 조회 실패가 프로젝트 생성 전체를 막는 것은 과하다.
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
                                          modelId || undefined);
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
      <div className="sm:w-44">
        <label htmlFor="pmodel" className="block text-xs text-slate-500 mb-1">
          AI 모델
        </label>
        {/* 이름만 보여준다 — 모델 id는 value로만 간다. */}
        <select
          id="pmodel"
          value={modelId}
          disabled={models.length === 0}
          onChange={(e) => setModelId(e.target.value)}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 bg-white disabled:bg-slate-50 disabled:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-400"
        >
          {models.length === 0 && <option value="">기본 모델</option>}
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>{m.name}</option>
          ))}
        </select>
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
