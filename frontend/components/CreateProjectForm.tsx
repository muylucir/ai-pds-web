"use client";
import { useEffect, useState } from "react";
import { createProject, ApiError } from "@/lib/api/client";
import { listModels, type ModelOption } from "@/lib/api/models";
import type { ProjectSummary } from "@/lib/api/types";
import { DEFAULT_LOCALE, type Locale } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

// 프로젝트 id에서 허용하지 않는 문자. id는 S3 키 프리픽스이자 로컬 워크스페이스
// 디렉토리 이름이고 URL 경로 세그먼트로도 들어가므로, 공백·슬래시·한글 같은
// 값이 들어가면 인코딩과 경로 문제로 나중에 터진다. 입력 단계에서 걸러 낸다.
const ID_DISALLOWED = /[^A-Za-z0-9_-]/g;

export function CreateProjectForm({ onCreated }: { onCreated: (p: ProjectSummary) => void }) {
  const t = useT();
  const [projectId, setProjectId] = useState("");
  // 입력에서 문자를 걸러 냈는지. 조용히 지우기만 하면 사용자는 자기 키보드가
  // 씹혔다고 생각한다 — 걸러 냈을 때만 규칙을 알려 준다. 한 번 켜지면 입력을
  // 비우거나 생성에 성공할 때까지 유지한다: 다음 글자가 유효하다고 바로 지우면
  // 안내가 한 글자만 스쳐 지나가 읽을 수 없다.
  const [idRejected, setIdRejected] = useState(false);
  const [name, setName] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelId, setModelId] = useState("");
  // 생성물 언어. UI 로케일과 무관하게 ko로 시작한다 — 이 값은 문서·프로토타입·
  // 채팅의 언어이고, 영어 UI를 쓰는 사람이 한국어 프로젝트를 만드는 것이
  // 정상이다. UI 로케일을 기본값으로 쓰면 그 선택을 조용히 대신 하게 된다.
  const [language, setLanguage] = useState<Locale>(DEFAULT_LOCALE);
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
        {/* 붙여넣기·자동완성도 onChange를 타므로 여기서 한 번 걸러 내면
            경로가 모두 덮인다. */}
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
      <div className="sm:w-44">
        <label htmlFor="pmodel" className="block text-xs text-slate-500 mb-1">
          {t("header.modelBadgeTitleShort")}
        </label>
        {/* 이름만 보여준다 — 모델 id는 value로만 간다. */}
        <select
          id="pmodel"
          value={modelId}
          disabled={models.length === 0}
          onChange={(e) => setModelId(e.target.value)}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 bg-white disabled:bg-slate-50 disabled:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-400"
        >
          {models.length === 0 && <option value="">{t("project.defaultModel")}</option>}
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>{m.name}</option>
          ))}
        </select>
      </div>
      <div className="sm:w-36">
        <label htmlFor="plang" className="block text-xs text-slate-500 mb-1">
          {t("project.language")}
        </label>
        {/* 생성 후 바꿀 수 없다 — 진행 중에 바꾸면 이미 만들어진 문서와
            트랜스크립트가 이전 언어로 남아 한 프로젝트 안에서 섞인다. */}
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
      {/* API 오류가 있으면 그쪽이 우선이다 — 걸러 낸 문자 안내보다 중요하다. */}
      {(error ?? (idRejected ? t("project.idCharsHint") : null)) && (
        <p className="text-sm text-rose-600 w-full sm:w-auto">
          {error ?? t("project.idCharsHint")}
        </p>
      )}
    </form>
  );
}
