"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { AppHeader } from "@/components/AppHeader";
import { CreateProjectForm } from "@/components/CreateProjectForm";
import { ProjectList } from "@/components/ProjectList";
import { listProjects } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useT } from "@/lib/i18n/provider";

export default function Home() {
  const t = useT();
  const router = useRouter();
  const [page, setPage] = useState(1);
  const { data, error, loading, reload } = useAsync(() => listProjects(page), [page]);
  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="max-w-7xl mx-auto px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">{t("list.title")}</h1>
          <p className="text-sm text-slate-500 mt-1">
            {t("list.subtitle")}
          </p>
        </div>
        {/* 생성 성공 = 곧바로 그 프로젝트의 대시보드로 — 목록에 추가만 되는
            것보다 워크숍 시작 흐름이 자연스럽다. */}
        <CreateProjectForm onCreated={(p) => router.push(`/projects/${p.project_id}/dashboard`)} />
        {loading && <p className="text-sm text-slate-400">{t("page.loading")}</p>}
        {error && (
          <p className="text-sm text-rose-600">
            {t("list.loadFailed")}
          </p>
        )}
        {data && (
          <ProjectList
            data={data}
            onDeleted={() => {
              // 삭제로 현재 페이지가 비면 이전 페이지로 (page 상태 변경이 곧 리로드)
              if (data.projects.length === 1 && page > 1) setPage(page - 1);
              else reload();
            }}
            onPageChange={setPage}
          />
        )}
      </main>
    </>
  );
}
