"use client";
import Link from "next/link";
import { useT } from "@/lib/i18n/provider";

/** 아무 워크플로우도 돌지 않은 프로젝트의 대시보드에 뜨는 첫 안내.
 *
 * **왜 필요한가.** 프로젝트를 만들면 첫 화면이 대시보드이고, 그 화면의 모든
 * 숫자가 0이고 스테이지 목록·산출물·활동이 전부 비어 있다. 무엇을 해야
 * 하는지에 대한 유일한 단서는 질문 기록 카드 아래의 작은 회색 한 줄
 * (`dash.questionRecordsHint`)뿐이었고, 실제 사용자가 그 화면에서 당황했다.
 *
 * **왜 닫기 버튼이 없는가.** 이 배너는 일이 시작되면 스스로 사라진다 —
 * 사용자가 치울 이유가 없고, 닫기를 붙이면 "아직 시작 안 했는데 안내가
 * 없는" 상태를 사용자가 스스로 만들 수 있다.
 */
export function GetStartedBanner({ projectId }: { projectId: string }) {
  const t = useT();
  return (
    <section
      className="mb-8 rounded-xl border border-violet-200 bg-violet-50 px-6 py-5 flex flex-wrap items-center justify-between gap-4"
      aria-labelledby="get-started-heading"
    >
      <div className="min-w-0">
        <h2 id="get-started-heading" className="font-bold text-violet-900">
          {t("dash.notStartedTitle")}
        </h2>
        <p className="mt-1 text-sm text-violet-800">{t("dash.notStartedBody")}</p>
      </div>
      <Link
        href={`/projects/${projectId}/workspace`}
        className="shrink-0 inline-flex items-center gap-1.5 px-4 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium"
      >
        {t("dash.notStartedCta")}
      </Link>
    </section>
  );
}
