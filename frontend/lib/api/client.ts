import { CREDENTIALS } from "@/lib/auth";
import type { ApprovalEvidence, ApprovalRecord } from "@/lib/approvalState";
import type {
  AuditEntry,
  ContextUsage,
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
  /** The id of **the turn already running** that a 409 `turn_in_progress` reports. What the rejected side should do
   *  is not resend but watch that turn (start_turn in backend routes/turns.py). */
  turnId?: string;
  constructor(status: number, detail: string, turnId?: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.turnId = turnId;
  }
}

/** Convert a failure response into ApiError. `detail` is either a string or `{code, turn_id}` —
 *  the latter puts the code in `detail` and the turn id in `turnId`. If it is not JSON (a 431
 *  from the proxy, etc.), statusText is kept.
 *
 *  Any other shape (such as FastAPI's default 422 array of error objects) is left as `bad_request` —
 *  converting an object to a string would show "[object Object]" on screen. Both fetch wrappers (request
 *  here and apiFetch in http.ts) read through this one function. */
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
    } else if (d !== undefined && d !== null) {
      detail = "bad_request";
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
  // When unspecified, the key is omitted entirely — it matches the server's optional field, and the
  // result is the same as sending null, so we choose to send less.
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

/** Replace the viewing member list wholesale. Values are emails or usernames, and the server
 *  converts them to usernames for storage. Owner/admin only. */
export async function setProjectMembers(pid: string, members: string[]): Promise<ProjectDetail> {
  return request<ProjectDetail>(`/projects/${encodeURIComponent(pid)}/members`, {
    method: "PUT", body: JSON.stringify({ members }),
  });
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

// Approval history + current document hash — the primary basis for gate judgment
// (see lib/approvalState.ts). Audit log parsing is used only as a fallback for existing
// projects whose history is empty.
//
// The hash is not computed on the frontend: if the algorithm exists in two places, the moment they
// diverge, approvals silently go unrecognized (the gate just does not open, so tracing the cause is hard).
// The return shape is exactly deriveApprovalState's second argument (ApprovalEvidence).
export async function listApprovals(pid: string): Promise<ApprovalEvidence> {
  const r = await request<{ approvals: ApprovalRecord[]; current_doc_hash: string | null }>(
    `/projects/${encodeURIComponent(pid)}/approvals`);
  return { approvals: r.approvals, currentDocHash: r.current_doc_hash };
}

/** Approve a document. The backend writes the record first and then runs the agent turn.
 *
 *  Why there is no body: **the backend** decides the document to approve and its hash. Trusting
 *  values sent by the screen could approve content different from (staler than) what the user was looking at.
 */
/** Approve. Leaves a record and **only starts** the next stage's turn — the workspace attaches to and
 *  watches that turn via `GET /turn`. If a turn is running, it is a 409 `turn_in_progress`. */
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

// GET /projects/{pid}/artifacts/missing-references → the files the Discovery
// Document cites as evidence but the artifacts do not contain. `document` is
// null until that document exists (nothing to cite yet).
export interface MissingReferences {
  document: string | null;
  missing: string[];
}

export async function getMissingReferences(pid: string): Promise<MissingReferences> {
  return request<MissingReferences>(
    `/projects/${encodeURIComponent(pid)}/artifacts/missing-references`);
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

// A copy of a document taken when the user handed over answers (backend aipds/doc_revisions.py).
// `existed: false` — the document was written after those answers, so there is no earlier text.
export interface DocBaseline {
  id: string;
  at: string;
  kind: "answers" | "revision";
  source: string;
  existed: boolean;
}

// GET /projects/{pid}/revisions?path= → baselines that differ from the current text, newest first.
export async function listDocBaselines(pid: string, path: string): Promise<DocBaseline[]> {
  const r = await request<{ baselines: DocBaseline[] }>(
    `/projects/${encodeURIComponent(pid)}/revisions?path=${encodeURIComponent(path)}`);
  return r.baselines;
}

// GET /projects/{pid}/revisions/{id}?path= → the text in that round (null if it did not exist yet).
export async function readDocBaseline(pid: string, id: string, path: string): Promise<string | null> {
  const r = await request<{ content: string | null }>(
    `/projects/${encodeURIComponent(pid)}/revisions/${encodeURIComponent(id)}?path=${encodeURIComponent(path)}`);
  return r.content;
}

// POST /projects/{pid}/interrupt → 202. Cuts off the running turn. The interrupt has not finished
// by the time of the response (202) — the stream reports it with a terminal event.
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

/** This project's current (or just finished) turn. When the workspace opens, it uses this to decide
 *  whether to attach. `interrupted` is a turn cut off by a backend restart (backend turn_marker.py). */
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

// GET /projects/{pid}/context → the last turn's context usage (null if none). Restored when the
// screen opens — a running turn overwrites it with `context` events.
export async function getContext(pid: string): Promise<ContextUsage | null> {
  const r = await request<{ context: ContextUsage | null }>(
    `/projects/${encodeURIComponent(pid)}/context`);
  return r.context;
}

// POST /projects/{pid}/uploads (multipart `file`) → the stored workspace path
// (+ the original kept beside a converted PDF) + char count/truncation flag. Deliberately bypasses the
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
): Promise<{ path: string; original: string | null; chars: number; truncated: boolean }> {
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
