// frontend/lib/api/prototypes.ts — client for Task 7's prototype build/host
// REST+SSE contract (backend/pathfinder/routes/prototypes.py).
//
// client.ts's private `request<T>()` always JSON-decodes 2xx responses ("204
// bodies aren't used by this contract" per its own comment) — but several
// endpoints here DO answer 204 (close session, submit answers, stop host).
// That helper isn't exported, so this file carries its own small fetch
// wrapper that mirrors client.ts's shape (credentials, Content-Type-only-
// with-a-body, ApiError-on-!ok) plus the one extra rule client.ts doesn't
// need: a 204 resolves to `undefined` instead of calling res.json().
import { CREDENTIALS } from "@/lib/auth";
import { API_BASE_URL, ApiError } from "./client";
import type { StreamHandlers } from "./sse";
import type { AgentEvent } from "./types";

export type PrototypeState = "none" | "building" | "built" | "running" | "failed";

export interface PrototypeInfo {
  slug: string;
  spec_path: string;
  state: PrototypeState;
  port: number | null;
}

// Mirrors backend/pathfinder/proto/host.py's HostState Literal exactly.
export type HostState = "installing" | "building" | "running" | "failed" | "stopped";

export interface HostStatus {
  state: HostState;
  port: number | null;
  log_tail: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (init?.body !== undefined && headers["Content-Type"] === undefined) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, credentials: CREDENTIALS });
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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function sessionPath(pid: string, slug: string, suffix = ""): string {
  return `/projects/${encodeURIComponent(pid)}/prototypes/${encodeURIComponent(slug)}${suffix}`;
}

export interface PrototypeListing {
  prototypes: PrototypeInfo[];
  /** Concurrent builds in flight backend-wide, and the cap. New with
   *  in-process builds: MicroVM builds had no ceiling, so a card needs to be
   *  able to explain a 429 before the user clicks. */
  active_builds: number;
  max_builds: number;
}

export async function listPrototypes(pid: string): Promise<PrototypeListing> {
  return request<PrototypeListing>(`/projects/${encodeURIComponent(pid)}/prototypes`);
}

/** Plain URL, not a Blob fetch: the browser handles Content-Disposition and
 *  the filename, matching how surveyCsvUrl is consumed via <a href>. */
export function prototypeArchiveUrl(pid: string, slug: string): string {
  return `${API_BASE_URL}${sessionPath(pid, slug, "/archive")}`;
}

export async function startSession(pid: string, slug: string): Promise<{ status: string }> {
  return request<{ status: string }>(sessionPath(pid, slug, "/session"), { method: "POST" });
}

export async function closeSession(pid: string, slug: string): Promise<void> {
  await request<void>(sessionPath(pid, slug, "/session"), { method: "DELETE" });
}

export async function interruptSession(pid: string, slug: string): Promise<{ status: string }> {
  return request<{ status: string }>(sessionPath(pid, slug, "/interrupt"), { method: "POST" });
}

// POST /answers → 204 on success, 409 if there's no pending question to
// resolve. interrupt_id is server-owned (captured off the open events
// stream) — the client never sees or sends one, same pattern as
// useWorkspaceStream.submitAnswers's document-review twin.
export async function submitPrototypeAnswers(
  pid: string,
  slug: string,
  answers: Record<string, string>,
): Promise<boolean> {
  try {
    await request<void>(sessionPath(pid, slug, "/answers"), {
      method: "POST",
      body: JSON.stringify({ answers }),
    });
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) return false;
    throw err;
  }
}

export async function startHost(pid: string, slug: string): Promise<HostStatus> {
  return request<HostStatus>(sessionPath(pid, slug, "/host"), { method: "POST" });
}

export async function stopHost(pid: string, slug: string): Promise<void> {
  await request<void>(sessionPath(pid, slug, "/host"), { method: "DELETE" });
}

// GET /host → 404 when nothing's hosted; that's a normal, expected state for
// this feature (not an error) so it's collapsed to null rather than thrown.
export async function getHost(pid: string, slug: string): Promise<HostStatus | null> {
  try {
    return await request<HostStatus>(sessionPath(pid, slug, "/host"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

// The reverse-proxied URL for a running prototype (routes.py's
// proxy_prototype, mounted under /api in prod — see client.ts:13).
export function prototypePreviewUrl(pid: string, slug: string): string {
  return `${API_BASE_URL}/proto/${encodeURIComponent(pid)}/${encodeURIComponent(slug)}/`;
}

// Opens GET /prototypes/{slug}/events?text=... as an SSE stream — the
// prototype-session twin of sse.ts's streamEvents/streamAnswers. sse.ts's
// own `openStream` isn't exported, so this mirrors its ~30-line shape
// (parse each frame's `data` as AgentEvent, finish on "done"/"error" or a
// transport error, close the EventSource either way) rather than duplicating
// its logic behind a private import that doesn't exist.
export function streamPrototypeEvents(
  pid: string,
  slug: string,
  text: string,
  handlers: StreamHandlers,
): () => void {
  const es = new EventSource(
    `${API_BASE_URL}${sessionPath(pid, slug, "/events")}?text=${encodeURIComponent(text)}`,
  );

  const close = () => es.close();

  es.onmessage = (ev: MessageEvent) => {
    let parsed: AgentEvent;
    try {
      parsed = JSON.parse(ev.data) as AgentEvent;
    } catch (err) {
      handlers.onError?.(err);
      return;
    }
    handlers.onEvent(parsed);
    if (parsed.kind === "done" || parsed.kind === "error") {
      close();
      handlers.onDone();
    }
  };

  es.onerror = (err) => {
    close();
    handlers.onError?.(err);
    handlers.onDone();
  };

  return close;
}
