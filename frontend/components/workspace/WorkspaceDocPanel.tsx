"use client";
// frontend/components/workspace/WorkspaceDocPanel.tsx
"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { listArtifacts, readArtifact, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { Markdown } from "@/components/Markdown";
import { useT } from "@/lib/i18n/provider";

// The workspace's 4th column. Renders the document the CONVERSATION is
// currently about (activeDoc — submit_document 이벤트뿐 아니라 doc성
// file_changed도 추적, ui-bug2 싱크 수정) inline so the user reads it
// without leaving the workspace.
//
// turnSeq: 턴이 끝날 때마다 증가하는 시퀀스. 문서 이벤트가 도착한 시점에는
// VM→S3 동기화 전이라 읽기가 비거나 404일 수 있으므로, 턴 종료 시점에
// 다시 읽는다 (fetch 키에 포함).
//
// Hidden below `lg` — the same responsive posture as StageSidebar and
// WorkspaceRightPanel; on narrow screens the review route is the fallback.
export function WorkspaceDocPanel({
  projectId,
  activeDoc,
  turnSeq,
  changedPaths,
}: {
  projectId: string;
  activeDoc: { path: string; version: string | null } | null;
  turnSeq: number;
  // 이번 턴에 쓰인 경로들(`file_changed`). **목록 재조회의 트리거다.**
  //
  // 종전 키는 `[projectId, turnSeq, activeDoc?.path]`였고 "파일이 바뀌었다"는
  // 실시간 신호가 없었다 — `turnSeq`는 턴이 끝날 때 오르고 `activeDoc`은 문서
  // 이벤트에만 바뀐다. 그래서 감사·상태·질문 파일만 쓰는 턴에서는 목록이 마운트
  // 시점 값(새 프로젝트면 빈 배열)에 머물고, 오른쪽 "최근 산출물"만 채워졌다 —
  // 드롭다운이 아예 안 뜨는 실측 증상이 그 모양이다.
  //
  // 이 신호로 거는 것이 안전한 근거: 훅이 **게시하고 나서 광고한다**
  // (claude_driver._on_post_tool_use의 publish_file → file_changed). 즉 이 배열에
  // 경로가 들어온 시점에 그 파일은 이미 정본에 있으므로 재조회가 반드시 찾는다.
  //
  // **옵셔널이 아니다.** 기본값 `[]`을 두면 부모가 안 넘겨도 컴파일되고, 그러면 이
  // 재조회가 프로덕션에서만 조용히 죽는다 — 필수로 두어 타입체크가 배선을 지킨다.
  changedPaths: string[];
}) {
  const t = useT();
  // 드롭다운 선택 상태. null = activeDoc 따름. activeDoc이 바뀌면(새 문서
  // 이벤트) 수동 선택을 리셋해 대화를 따라간다 — 스펙의 우선순위.
  const [manualPath, setManualPath] = useState<string | null>(null);
  useEffect(() => {
    setManualPath(null);
  }, [activeDoc?.path]);
  const version = activeDoc?.version ?? null;

  // 산출물 목록 — 턴 종료(turnSeq)와 **새 문서가 생길 때마다**(activeDoc.path)
  // 재조회한다.
  //
  // `activeDoc?.path`를 키에 넣는 이유(2026-08-18): 백엔드가 이제 쓰기 직후에
  // 문서를 정본에 게시하므로(backend/aipds/workspace_sync.py) 턴 중에도 목록이
  // 정확할 수 있다. 턴 종료만 기다리면 한 턴에 문서 5개를 쓰는 동안 드롭다운에는
  // 현재 문서 하나(아래 union)만 보이고, 앞서 쓴 것들은 정본에 있는데도 목록에
  // 없다 — 실측한 증상 중 "잠깐 나타났다 사라진다"가 그 모양이다.
  //
  // 목록이 아직 path를 포함하지 않을 수 있으므로(이벤트가 게시보다 먼저 도착하는
  // 짧은 창) 현재 path를 항상 union — 드롭다운/본문 미스매치 및 "목록이 비어
  // 있으면 select 자체가 숨겨지는" 문제를 함께 해결한다.
  const artifacts = useAsync(() => listArtifacts(projectId),
                             [projectId, turnSeq, activeDoc?.path ?? "",
                              changedPaths.length]);
  const listed = artifacts.data ?? [];
  // **목록으로 떨어지는 폴백.** 종전에는 `manualPath ?? activeDoc?.path ?? null`이라
  // `activeDoc`이 없으면 본문이 비었다 — 드롭다운은 `listed`로 채워지므로 "옵션은
  // 가득한데 본문은 '아직 문서가 없습니다'"가 구조적으로 가능했고, 새로고침 후에는
  // `activeDoc`이 항상 null이라 늘 그 상태였다.
  //
  // 첫 항목을 고르는 것이 의미를 갖는 이유: 백엔드가 최신 순으로 준다
  // (Workspace.list_artifacts). 알파벳 순이던 동안은 첫 항목이 거의 항상
  // `aiplc-docs/audit.md`였다.
  const path = manualPath ?? activeDoc?.path ?? listed[0] ?? null;
  const options = path && !listed.includes(path) ? [...listed, path] : listed;

  // 404 → "아직 동기화 안 됨"으로 구분해서 들고 있는다. 턴 중에는 정상(이벤트가
  // 동기화보다 먼저 도착) 이지만, 턴이 끝난 뒤에도 404라면 그 문서는 S3에 없다 —
  // 예전에는 이걸 빈 문서로 렌더해서 "생성됐다는데 내용이 없다"로 보였다.
  const MISSING = Symbol.for("doc-missing");
  const content = useAsync(
    () =>
      path === null
        ? Promise.resolve("")
        : readArtifact(projectId, path).catch((e) =>
            e instanceof ApiError && e.status === 404
              ? (MISSING as unknown as string)
              : Promise.reject(e),
          ),
    // turnSeq in the key: 턴 종료마다 재읽기 (턴 중 동기화 지연 보정).
    [projectId, path, turnSeq],
  );
  const missing = content.data === (MISSING as unknown as string);
  const text = missing ? "" : (content.data ?? "");

  const loadError = path !== null && content.error !== null;
  // Version strings from the backend may already carry a "v" prefix (e.g.
  // "v2") or not (e.g. "2"); normalize to a single leading "v".
  const versionLabel = version ? (/^v/i.test(version) ? version : `v${version}`) : null;

  return (
    <aside
      aria-label={t("ws.generatedDocsAria")}
      className="hidden lg:flex flex-col min-w-0 min-h-0 bg-white border-l border-slate-200"
    >
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-2">
        {options.length > 0 ? (
          <select
            aria-label={t("ws.selectDocAria")}
            value={path ?? ""}
            onChange={(e) => setManualPath(e.target.value)}
            className="min-w-0 flex-1 text-xs font-bold text-slate-600 bg-transparent border border-slate-200 rounded-lg px-2 py-1.5 truncate focus:outline-none focus:ring-2 focus:ring-violet-300"
          >
            {options.map((p) => (
              <option key={p} value={p}>
                {p.slice(p.lastIndexOf("/") + 1)}
              </option>
            ))}
          </select>
        ) : (
          <p className="text-xs font-bold text-slate-400 uppercase tracking-wide truncate">{t("ws.generatedDocs")}</p>
        )}
        {path !== null && (
          <button
            type="button"
            aria-label={t("ws.refreshDocAria")}
            disabled={content.loading}
            onClick={() => {
              // 현재 문서와 산출물 목록을 함께 재조회 — 문서만 갱신하면 방금
              // 생성된 다른 문서가 드롭다운에 반영되지 않는다.
              content.reload();
              artifacts.reload();
            }}
            className="shrink-0 w-7 h-7 rounded-lg text-slate-400 hover:text-violet-600 hover:bg-violet-50 disabled:opacity-40 flex items-center justify-center"
          >
            ↻
          </button>
        )}
        {versionLabel && manualPath === null && (
          <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">
            {versionLabel}
          </span>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4 text-sm text-slate-700">
        {/* 목록 실패를 "문서가 없다"로 뭉개지 않는다 — 화면이 같으면 원인을 영영 못
            본다(이 파일이 docUnsaved와 docEmpty를 가른 것과 같은 규율). 문서가
            선택돼 있으면 그 본문이 우선이다: 목록이 실패해도 읽을 수 있는 문서가
            있으면 그것을 막을 이유가 없다. */}
        {path === null && artifacts.error !== null ? (
          <p className="text-rose-600">{t("ws.docListFailed")}</p>
        ) : path === null ? (
          <p className="text-slate-400">
            {t("ws.noDocsYet")}
          </p>
        ) : loadError ? (
          <p className="text-rose-600">{t("ws.docLoadFailed")}</p>
        ) : content.loading ? (
          <p className="text-slate-400">{t("ws.docLoading")}</p>
        ) : missing ? (
          // 저장 자체가 안 된 상태 — "비어 있음"과 구분해서 알린다. 이걸 빈
          // 문서로 뭉개면 사용자는 문서가 만들어졌다고 믿고, 새로고침하면
          // 목록에서 사라진 이유를 알 수 없다.
          <p className="text-amber-700">{t("ws.docUnsaved")}</p>
        ) : text.trim() === "" ? (
          <p className="text-slate-400">{t("ws.docEmpty")}</p>
        ) : (
          <Markdown text={text} />
        )}
      </div>
      {path !== null && (
        <div className="p-3 border-t border-slate-100">
          <Link
            href={`/projects/${projectId}/review`}
            className="text-xs font-medium text-violet-700 underline hover:text-violet-900"
          >
            {t("ws.toFullReview")}
          </Link>
        </div>
      )}
    </aside>
  );
}
