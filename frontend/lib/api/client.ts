import { CREDENTIALS } from "@/lib/auth";
import type { ApprovalEvidence, ApprovalRecord } from "@/lib/approvalState";
import type {
  AuditEntry,
  HistoryItem,
  ProjectDetail,
  ProjectPage,
  ProjectState,
  ProjectSummary,
  TurnSummary,
} from "./types";

// The ONE place the base URL lives. No trailing slash.
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  detail: string;
  /** 409 `turn_in_progress`가 알려 주는 **이미 도는 턴**의 id. 거절된 쪽이 할 일은
   *  다시 보내기가 아니라 그 턴을 보는 것이다(backend routes/turns.py의 start_turn). */
  turnId?: string;
  constructor(status: number, detail: string, turnId?: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.turnId = turnId;
  }
}

/** 실패 응답을 ApiError로 옮긴다. `detail`은 문자열이거나 `{code, turn_id}`다 —
 *  후자는 코드를 `detail`로, 턴 id를 `turnId`로 싣는다. JSON이 아니면(프록시가 낸
 *  431 등) statusText를 유지한다. */
export async function apiErrorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  let turnId: string | undefined;
  try {
    const body = await res.json();
    const d = body?.detail;
    if (typeof d === "string") detail = d;
    else if (d && typeof d.code === "string") {
      detail = d.code;
      if (typeof d.turn_id === "string") turnId = d.turn_id;
    }
  } catch {
    // non-JSON error body — keep statusText
  }
  return new ApiError(res.status, detail, turnId);
}

// Encode a {name:path} value segment-by-segment: escape each segment but keep
// the "/" separators so the backend's `{name:path}` route receives the full
// relative workspace path intact.
function encodePath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  // Only set Content-Type when there's a body — avoids a needless CORS preflight on GETs.
  if (init?.body !== undefined && headers["Content-Type"] === undefined) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: CREDENTIALS,
  });
  if (!res.ok) throw await apiErrorFrom(res);
  // 204/empty bodies aren't used by this contract; every 2xx here returns JSON.
  return (await res.json()) as T;
}

export async function createProject(projectId: string, name?: string,
                                    modelId?: string,
                                    language?: string): Promise<ProjectSummary> {
  const body: { project_id: string; name?: string; model_id?: string;
                language?: string } = {
    project_id: projectId,
  };
  if (name !== undefined) body.name = name;
  // 미지정은 키를 아예 빼서 보낸다 — 서버의 optional 필드와 맞고, null을 보내는
  // 것과 결과가 같으므로 더 적게 보내는 쪽을 고른다.
  if (modelId !== undefined) body.model_id = modelId;
  if (language !== undefined) body.language = language;
  return request<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify(body) });
}

export async function getProject(pid: string): Promise<ProjectDetail> {
  return request<ProjectDetail>(`/projects/${encodeURIComponent(pid)}`);
}

export async function listProjects(page = 1, size = 10): Promise<ProjectPage> {
  return request<ProjectPage>(`/projects?page=${page}&size=${size}`);
}

export async function deleteProject(projectId: string): Promise<void> {
  await request<{ deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  });
}

export async function getState(pid: string): Promise<ProjectState> {
  return request<ProjectState>(`/projects/${encodeURIComponent(pid)}/state`);
}

export async function getAudit(pid: string): Promise<AuditEntry[]> {
  return request<AuditEntry[]>(`/projects/${encodeURIComponent(pid)}/audit`);
}

// 승인 이력 + 현재 문서 해시 — 게이트 판정의 1순위 근거다
// (lib/approvalState.ts 참조). 감사 로그 파싱은 이력이 비어 있는 기존
// 프로젝트를 위한 폴백으로만 쓴다.
//
// 해시를 프론트에서 계산하지 않는다: 알고리즘이 두 곳에 생기면 어긋나는 순간
// 승인이 조용히 인식되지 않는다(게이트가 안 열릴 뿐이라 원인 추적이 어렵다).
// 반환 형태가 그대로 deriveApprovalState의 두 번째 인자(ApprovalEvidence)다.
export async function listApprovals(pid: string): Promise<ApprovalEvidence> {
  const r = await request<{ approvals: ApprovalRecord[]; current_doc_hash: string | null }>(
    `/projects/${encodeURIComponent(pid)}/approvals`);
  return { approvals: r.approvals, currentDocHash: r.current_doc_hash };
}

/** 문서를 승인한다. 백엔드가 레코드를 먼저 쓰고 그 다음 에이전트 턴을 돌린다.
 *
 *  본문이 없는 이유: 승인 대상 문서와 그 해시를 **백엔드가** 정한다. 화면이
 *  보낸 값을 믿으면 사용자가 보고 있던 것과 다른(낡은) 내용을 승인할 수 있다.
 */
/** 승인한다. 레코드를 남기고 다음 단계의 턴을 **시작만** 한다 — 그 턴은 워크스페이스가
 *  `GET /turn`으로 붙어 본다. 도는 턴이 있으면 409 `turn_in_progress`다. */
export async function approveDocument(pid: string): Promise<{ turnId: string }> {
  const r = await request<{ approved: boolean; turn_id: string }>(
    `/projects/${encodeURIComponent(pid)}/approve`, { method: "POST" });
  return { turnId: r.turn_id };
}

export async function listQuestionFiles(pid: string): Promise<string[]> {
  const r = await request<{ questions: string[] }>(`/projects/${encodeURIComponent(pid)}/questions`);
  return r.questions;
}

export async function listArtifacts(pid: string): Promise<string[]> {
  const r = await request<{ artifacts: string[] }>(`/projects/${encodeURIComponent(pid)}/artifacts`);
  return r.artifacts;
}

// GET /projects/{pid}/files/{path} → { content } — general-purpose reader for
// the review page's document tree. Backend only allows aiplc-docs/ paths
// (403 otherwise); this is a display-only viewer, not a generic file API.
export async function readArtifact(pid: string, path: string): Promise<string> {
  const r = await request<{ content: string }>(
    `/projects/${encodeURIComponent(pid)}/files/${encodePath(path)}`,
  );
  return r.content;
}

// POST /projects/{pid}/interrupt → 202. 진행 중인 턴을 끊는다. 응답 시점에
// 중단이 끝나 있지 않다(202) — 스트림이 종결 이벤트로 알린다.
export async function interruptTurn(pid: string): Promise<void> {
  await request<{ status: string }>(`/projects/${encodeURIComponent(pid)}/interrupt`, {
    method: "POST",
  });
}

// GET /projects/{pid}/pending → { pending: string | null }. The raw JSON
// string (Task 9's QuestionsPayload shape) is returned as-is — callers parse
// it with the same safeParse fallback used for streamed "questions" events.
export async function getPending(pid: string): Promise<string | null> {
  const r = await request<{ pending: string | null }>(`/projects/${encodeURIComponent(pid)}/pending`);
  return r.pending;
}

/** 이 프로젝트의 현재(또는 방금 끝난) 턴. 워크스페이스가 열릴 때 이것으로 붙을지
 *  정한다. `interrupted`는 백엔드 재시작으로 끊긴 턴이다(backend turn_marker.py). */
export async function getTurn(pid: string): Promise<TurnSummary | null> {
  const r = await request<{ turn: TurnSummary | null }>(
    `/projects/${encodeURIComponent(pid)}/turn`);
  return r.turn;
}

// GET /projects/{pid}/history → { items: HistoryItem[] } (Task 1) — restores
// the chat timeline (user/ai messages + questions-card markers) on workspace
// mount.
export async function getHistory(pid: string): Promise<HistoryItem[]> {
  const r = await request<{ items: HistoryItem[] }>(`/projects/${encodeURIComponent(pid)}/history`);
  return r.items;
}

// POST /projects/{pid}/uploads (multipart `file`) → the stored workspace path
// + char count/truncation flag (Task 2, backend). Deliberately bypasses the
// shared `request()` helper: FormData needs the browser to set its own
// multipart Content-Type (with boundary) — setting it manually would break
// the request.
// GET /projects/{pid}/artifacts/archive → zip bytes of the whole aiplc-docs/
// tree. Bypasses the JSON `request()` helper (like uploadFile) since the
// response body is binary, not JSON.
export async function downloadArtifactsArchive(pid: string): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(pid)}/artifacts/archive`, {
    credentials: CREDENTIALS,
  });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.blob();
}

export async function uploadFile(
  pid: string,
  file: File,
): Promise<{ path: string; chars: number; truncated: boolean }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(pid)}/uploads`, {
    method: "POST",
    credentials: CREDENTIALS,
    body: form,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(res.status, detail || res.statusText);
  }
  return res.json();
}
