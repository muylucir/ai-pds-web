"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { AppHeader } from "@/components/AppHeader";
import { CreateProjectForm } from "@/components/CreateProjectForm";
import { ImportProjectButton } from "@/components/ImportProjectButton";
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
        {/* The right side of the title row is where import goes. Placing it beside the create form would give
            a rare action (moving a project over from another instance) the same weight as a common one
            (new project) — so it is folded into one button and handled in a modal. */}
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">{t("list.title")}</h1>
            <p className="text-sm text-slate-500 mt-1">
              {t("list.subtitle")}
            </p>
          </div>
          {/* Import follows the same flow as create — it goes straight to the restored project's
              dashboard. */}
          <ImportProjectButton
            onImported={(r) => router.push(`/projects/${r.project_id}/dashboard`)}
          />
        </div>
        {/* Successful create = go straight to that project's dashboard — a more natural start to the
            workshop than just adding it to the list. */}
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
              // If deletion empties the current page, go to the previous page (changing page state is itself the reload)
              if (data.projects.length === 1 && page > 1) setPage(page - 1);
              else reload();
            }}
            onChanged={reload}
            onPageChange={setPage}
          />
        )}
      </main>
    </>
  );
}
