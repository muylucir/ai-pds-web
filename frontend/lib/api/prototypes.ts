// frontend/lib/api/prototypes.ts — client for Task 7's prototype build/host
// REST+SSE contract (backend/aipds/routes/prototypes.py).
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
import { openStream, openViaHandle, type StreamHandlers } from "./sse";
import type { ContextUsage, HistoryItem } from "./types";

export type PrototypeState = "none" | "building" | "built" | "running" | "failed";

export interface PrototypeInfo {
  slug: string;
  /** The product name read from the spec body, used as the card title. null if the spec does
   *  not state a name, in which case the card falls back to `slug`.
   *
   *  **Why it is separate from `slug`.** The slug is an identifier (builds, resets, and surveys
   *  are keyed by that value). And the slug of the single-prototype layout is the constant
   *  `"prototype"` (`SINGLE_ID` in backend proto/layout.py), so it cannot serve as a title — it
   *  follows from the judgment that with only one thing to distinguish there is nothing to make
   *  a slug from, and so the name comes only from the body. */
  name: string | null;
  spec_path: string;
  state: PrototypeState;
  port: number | null;
  /** Whether a build session is open right now.
   *
   *  **Why it is separate from `state`.** Whether the server is up and whether a session is
   *  open are independent facts. While a single enum carried both, the session masked hosting:
   *  starting to modify a running prototype turned the card into `building`, and the preview
   *  and share link disappeared — the server stayed up, so only the screen was lying. Now
   *  `state` speaks only of the artifact and the server, and this value speaks of the session.
   *
   *  The card's build button branches on this value: if open, "세션 열기" (open session);
   *  otherwise "수정하기" (modify) / "이어서 하기" (continue) depending on the state. */
  session_open: boolean;
  /** Whether the running server is older than the source — that is, whether the preview and
   *  participant link are showing **a previous version**.
   *
   *  **`session_open` cannot stand in for it.** That signal disappears the moment the session
   *  closes, yet that is exactly when the user is most likely to wrongly assume "the change
   *  was applied". The server compares the time it read the source (`HostInfo.built_at`) with
   *  the latest source mtime of the build tree and sends the result down.
   *
   *  Always false unless `running` — there is no preview to go stale. */
  preview_stale: boolean;
  /** The access path containing the token (`/api/proto/t/{token}`). null if not hosting.
   *
   *  **The server builds it and sends it down** — the frontend does not assemble it from
   *  pid/slug. The token is not client state, and assembling it would require receiving the
   *  token itself as a separate field, so it would exist in places other than the link. It is a
   *  relative path, so when sharing externally it is made absolute with `absoluteShareUrl`. */
  access_url: string | null;
  /** Survey answers that a reset would destroy — shown in its confirmation. */
  response_count: number;
  /** Whether there is a survey that can take responses right now.
   *
   *  **It is a different question from `response_count > 0`.** That is 0 when there is no
   *  survey and also 0 when there is a survey with no responses yet, so without this field the
   *  card cannot tell the two states apart — in the measurement on ws-n, only 1 of 3
   *  prototypes had a survey, and the screen did not show that fact. It comes out of the same
   *  single pass in which the server already queries the survey tree (`survey_summary` in
   *  backend survey/store.py). */
  has_survey: boolean;
}

// Mirrors backend/aipds/proto/host.py's HostState Literal exactly.
export type HostState = "installing" | "building" | "running" | "failed" | "stopped";

export interface HostStatus {
  state: HostState;
  port: number | null;
  /** Same value as PrototypeInfo.access_url — null if not yet issued. */
  access_url: string | null;
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

// Wipes the prototype's build, session, transcript and survey — everything but
// the spec, so the card returns as a fresh buildable prototype. A 502 means a
// partial reset; every purge is idempotent, so retrying converges.
export async function resetPrototype(pid: string, slug: string): Promise<void> {
  await request<void>(sessionPath(pid, slug), { method: "DELETE" });
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

/** Turns the relative access path the server gave (`access_url`) into **an absolute URL that
 *  can be shared outside the app**.
 *
 *  Why the relative path cannot be copied as is: in deployment, access_url is
 *  `/api/proto/t/…`, and pasting that into a chat means nothing to the recipient.
 *  `new URL(..., origin)` is used so that when the value is **already an absolute URL**, as in
 *  local development, the origin is not prepended to make
 *  "http://localhost:3000http://localhost:8000/..." — base applies only to relative paths.
 *  (This judgment was carried over as is from the old prototypeShareUrl, and the test that pins
 *  that case remains.)
 *
 *  Absolutizing the server value without assembling a URL is all this function does: the path
 *  shape and the token are owned by the backend (access_url_path in routes/proto_public.py). */
export function absoluteShareUrl(accessUrl: string,
                                 origin: string = globalThis.location?.origin ?? ""): string {
  return new URL(accessUrl, origin).toString();
}

//: The first-turn sentinel. The server substitutes this value with session.first_prompt()
//: (_FIRST_TURN_SENTINEL in backend routes/prototypes.py) — both sides must have the same
//: value, so it is named here so that callers do not use the literal.
export const FIRST_TURN_SENTINEL = "__first__";

// Opens the prototype build stream for one turn: POST the text (starts the turn
// as a server job — backend aipds/turn_job.py), then watch it by id. Text rides
// in the POST body, not the URL: a long Korean message becomes a
// ~9-byte-per-char query string that pushed the request line past Node's 16KB
// maxHeaderSize and came back as HTTP 431 (lib/api/sse.ts's openViaHandle
// documents the measurement).
//
// The auto opening (the `__first__` sentinel) takes the same path too — it needs to receive the
// turn id to reattach to the same turn after a drop. The server replaces the sentinel with
// session.first_prompt() (_opening_text in backend routes/prototypes.py).
export function streamPrototypeEvents(
  pid: string,
  slug: string,
  text: string,
  handlers: StreamHandlers,
): () => void {
  const base = `${API_BASE_URL}${sessionPath(pid, slug, "/events")}`;
  return openViaHandle(
    sessionPath(pid, slug, "/turns"),
    { text },
    (turnId) => `${base}?turn=${encodeURIComponent(turnId)}`,
    handlers,
  );
}

/** Watch one build turn from after `after` — used for reattaching (drop, refresh) and replay. */
export function watchBuildTurn(
  pid: string,
  slug: string,
  turnId: string,
  after: number,
  handlers: StreamHandlers,
): () => void {
  const base = `${API_BASE_URL}${sessionPath(pid, slug, "/events")}`;
  return openStream(
    `${base}?turn=${encodeURIComponent(turnId)}&after=${after}`, handlers);
}

export interface BuildTurnSummary {
  turn_id: string;
  state: "running" | "done" | "error" | "interrupted";
  last_seq: number;
  /** What the user sent. null for the auto opening turn. */
  input: string | null;
}

/** The past build conversation (transcript), and the open session's turns to replay after it.
 *
 *  `items` is cut off **before** the open session's first turn — what follows is filled by
 *  replaying `turns` from the turn log (the completion card and an in-progress question form
 *  exist only on that path). Both come in one response, so a turn started in between is never
 *  shown twice or missed (backend routes/prototypes.get_history). Without a session, `turns` is
 *  an empty array. */
export interface BuildHistory {
  items: HistoryItem[];
  turns: BuildTurnSummary[];
  /** The last turn's context usage. null if the next conversation is a new session (improvement)
   *  — a closed session's value is wrong for that conversation (backend
   *  proto/history.load_context). */
  context?: ContextUsage | null;
}

export async function getBuildHistory(pid: string, slug: string): Promise<BuildHistory> {
  return request<BuildHistory>(sessionPath(pid, slug, "/history"));
}

/** The open build session and its turns. null if there is no session. When the build screen
 *  reopens, it uses this to replay the conversation from the start and attach to a running turn. */
export async function getBuildSession(
  pid: string, slug: string,
): Promise<{ status: string; turns: BuildTurnSummary[] } | null> {
  try {
    return await request<{ status: string; turns: BuildTurnSummary[] }>(
      sessionPath(pid, slug, "/session"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
