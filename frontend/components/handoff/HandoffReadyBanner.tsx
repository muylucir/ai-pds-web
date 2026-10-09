// Go-to-Market까지 끝났을 때 인계 탭으로 가는 문.
//
// **에이전트의 문장에 기대지 않는다.** GTM의 마지막 지시는 "Inception으로 진행"이고
// (상류 go-to-market.md Step 6), 그것은 이 웹에 없는 단계다. 프로토타입 배너가
// keumkang-v5 이후 같은 이유로 에이전트 문장이 아니라 디스크에서 유도되듯, 여기서는
// `aiplc-state.md`를 정규화한 capability(backend capabilities.py)의 go_to_market이
// completed인지를 본다. 스테이지 이름을 한국어로 쓴 프로젝트도 그쪽이 이미 묶는다.
//
// 닫기 버튼이 없다 — 프로토타입 배너와 같은 판단이다. Discovery가 끝난 뒤 다음으로 가는
// 문이고, 실수로 닫으면 사용자는 탭 줄의 "인계"를 스스로 찾아야 한다.
"use client";
import Link from "next/link";
import type { ProjectState } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

export function discoveryFinished(state: ProjectState | null | undefined): boolean {
  return !!state?.capabilities?.some((c) => c.key === "go_to_market" && c.status === "completed");
}

export function HandoffReadyBanner({ projectId, state, className }: {
  projectId: string;
  state: ProjectState | null | undefined;
  className?: string;
}) {
  const t = useT();
  if (!discoveryFinished(state)) return null;
  return (
    <div role="status"
         className={`rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 flex items-center justify-between gap-3 text-sm ${className ?? ""}`}>
      <span className="text-violet-900">{t("handoff.ready.banner")}</span>
      <Link href={`/projects/${projectId}/handoff`}
            className="shrink-0 rounded-lg bg-violet-600 px-3 py-1.5 font-medium text-white hover:bg-violet-700">
        {t("handoff.ready.cta")}
      </Link>
    </div>
  );
}
