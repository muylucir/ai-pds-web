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
import type { AgentEvent } from "./types";

export type PrototypeState = "none" | "building" | "built" | "running" | "failed";

export interface PrototypeInfo {
  slug: string;
  spec_path: string;
  state: PrototypeState;
  port: number | null;
  /** 토큰이 들어 있는 접근 경로(`/api/proto/t/{token}`). 호스팅 중이 아니면 null.
   *
   *  **서버가 만들어 내려보낸다** — 프론트가 pid/slug로 조립하지 않는다. 토큰은
   *  클라이언트 상태가 아니고, 조립하려면 토큰 자체를 별도 필드로 받아야 하므로
   *  링크가 아닌 곳에도 존재하게 된다. 상대 경로이므로 밖으로 공유할 때는
   *  `absoluteShareUrl`로 절대화한다. */
  access_url: string | null;
  /** Survey answers that a reset would destroy — shown in its confirmation. */
  response_count: number;
  /** 지금 응답을 받을 수 있는 설문이 있는가.
   *
   *  **`response_count > 0`과 다른 질문이다.** 설문이 없을 때도 0이고 설문이
   *  있는데 응답이 아직 없을 때도 0이라, 이 필드 없이는 카드가 두 상태를 구별할
   *  수 없다 — 실측 test2222에서 프로토타입 3개 중 1개에만 설문이 있었는데
   *  화면에 그 사실이 없었다. 서버가 설문 트리를 이미 조회하는 그 한 번에서
   *  함께 나온다(backend survey/store.py의 `survey_summary`). */
  has_survey: boolean;
}

// Mirrors backend/aipds/proto/host.py's HostState Literal exactly.
export type HostState = "installing" | "building" | "running" | "failed" | "stopped";

export interface HostStatus {
  state: HostState;
  port: number | null;
  /** PrototypeInfo.access_url과 같은 값 — 아직 발급되지 않았으면 null. */
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

/** 서버가 준 상대 접근 경로(`access_url`)를 **앱 밖으로 공유할 수 있는 절대
 *  URL**로.
 *
 *  상대 경로를 그대로 복사할 수 없는 이유: 배포에서 access_url은 `/api/proto/t/…`
 *  이고, 그것을 채팅에 붙이면 받는 사람에게는 아무 의미가 없다.
 *  `new URL(..., origin)`을 쓰는 것은 로컬 개발처럼 값이 **이미 절대 URL**일 때
 *  origin을 덧붙여 "http://localhost:3000http://localhost:8000/..."을 만들지 않기
 *  위해서다 — base는 상대 경로일 때만 적용된다. (구 prototypeShareUrl에서 그대로
 *  가져온 판단이고, 그 경우를 고정한 테스트도 남아 있다.)
 *
 *  URL을 조립하지 않고 서버 값을 절대화만 하는 것이 이 함수의 전부다: 경로 모양과
 *  토큰은 백엔드가 소유한다(routes/proto_public.py의 access_url_path). */
export function absoluteShareUrl(accessUrl: string,
                                 origin: string = globalThis.location?.origin ?? ""): string {
  return new URL(accessUrl, origin).toString();
}

//: 첫 턴의 센티널. 서버가 이 값을 session.first_prompt()로 치환한다
//: (backend routes/prototypes.py의 _FIRST_TURN_SENTINEL) — 양쪽이 같은 값이어야
//: 하므로 호출부가 리터럴을 쓰지 않게 여기서 이름을 준다.
export const FIRST_TURN_SENTINEL = "__first__";

// Opens the prototype build stream for one turn. Text rides in the POST body,
// not the URL: a long Korean message becomes a ~9-byte-per-char query string
// that pushed the request line past Node's 16KB maxHeaderSize and came back as
// HTTP 431 (lib/api/sse.ts's openViaHandle documents the measurement).
//
// The first turn keeps using the `?text=__first__` sentinel — it is 9 bytes and
// the server substitutes session.first_prompt() for it
// (backend routes/prototypes.py's _FIRST_TURN_SENTINEL).
export function streamPrototypeEvents(
  pid: string,
  slug: string,
  text: string,
  handlers: StreamHandlers,
): () => void {
  const base = `${API_BASE_URL}${sessionPath(pid, slug, "/events")}`;
  if (text === FIRST_TURN_SENTINEL) {
    return openStream(`${base}?text=${encodeURIComponent(text)}`, handlers);
  }
  return openViaHandle(
    sessionPath(pid, slug, "/turns"),
    { text },
    (turnId) => `${base}?turn=${encodeURIComponent(turnId)}`,
    handlers,
  );
}
