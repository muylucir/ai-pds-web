"use client";
import { useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CreateProjectForm } from "@/components/CreateProjectForm";
import { ProjectList } from "@/components/ProjectList";
import { listProjects } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function Home() {
  const [page, setPage] = useState(1);
  const { data, error, loading, reload } = useAsync(() => listProjects(page), [page]);
  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="max-w-7xl mx-auto px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">프로젝트</h1>
          <p className="text-sm text-slate-500 mt-1">
            워크숍 세션을 개설하고 Discovery를 시작하세요.
          </p>
        </div>
        <CreateProjectForm onCreated={reload} />
        {loading && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {error && (
          <p className="text-sm text-rose-600">
            프로젝트 목록을 불러오지 못했습니다. 백엔드 연결을 확인하세요.
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
