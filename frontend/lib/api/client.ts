import { getAuthToken } from "@/lib/auth";
import type {
  AuditEntry,
  ProjectState,
  ProjectSummary,
  QuestionFile,
  TurnResult,
} from "./types";

// The ONE place the base URL lives. No trailing slash.
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// Encode a {name:path} value segment-by-segment: escape each segment but keep
// the "/" separators so the backend's `{name:path}` route receives the full
// relative workspace path intact.
function encodePath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { "X-Project-Token": token } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...authHeaders(),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  // Only set Content-Type when there's a body — avoids a needless CORS preflight on GETs.
  if (init?.body !== undefined && headers["Content-Type"] === undefined) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body — keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  // 204/empty bodies aren't used by this contract; every 2xx here returns JSON.
  return (await res.json()) as T;
}

export async function createProject(projectId: string, name?: string): Promise<ProjectSummary> {
  const body: { project_id: string; name?: string } = { project_id: projectId };
  if (name !== undefined) body.name = name;
  return request<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify(body) });
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const r = await request<{ projects: ProjectSummary[] }>("/projects");
  return r.projects;
}

export async function getState(pid: string): Promise<ProjectState> {
  return request<ProjectState>(`/projects/${encodeURIComponent(pid)}/state`);
}

export async function getAudit(pid: string): Promise<AuditEntry[]> {
  return request<AuditEntry[]>(`/projects/${encodeURIComponent(pid)}/audit`);
}

export async function getDocument(pid: string): Promise<string> {
  const r = await request<{ markdown: string }>(`/projects/${encodeURIComponent(pid)}/document`);
  return r.markdown;
}

export async function listQuestionFiles(pid: string): Promise<string[]> {
  const r = await request<{ questions: string[] }>(`/projects/${encodeURIComponent(pid)}/questions`);
  return r.questions;
}

export async function getQuestionFile(pid: string, name: string): Promise<QuestionFile> {
  return request<QuestionFile>(`/projects/${encodeURIComponent(pid)}/questions/${encodePath(name)}`);
}

export async function putAnswers(
  pid: string,
  name: string,
  answers: Record<string, string>,
): Promise<QuestionFile> {
  return request<QuestionFile>(`/projects/${encodeURIComponent(pid)}/questions/${encodePath(name)}`, {
    method: "PUT",
    body: JSON.stringify({ answers }),
  });
}

export async function listArtifacts(pid: string): Promise<string[]> {
  const r = await request<{ artifacts: string[] }>(`/projects/${encodeURIComponent(pid)}/artifacts`);
  return r.artifacts;
}

export async function postMessage(pid: string, text: string): Promise<TurnResult> {
  return request<TurnResult>(`/projects/${encodeURIComponent(pid)}/message`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}
