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

export type PrototypeState = "none" | "building" | "built" | "running" | "failed";

export interface PrototypeInfo {
  slug: string;
  /** 카드 제목으로 쓰는, 명세 본문에서 읽은 제품명. 명세가 이름을 밝히지
   *  않으면 null이고, 그때 카드는 `slug`로 되돌아간다.
   *
   *  **`slug`와 별개인 이유.** slug는 식별자다(빌드·리셋·설문이 그 값으로
   *  키된다). 그리고 단일 프로토타입 레이아웃의 slug는 상수 `"prototype"`이라
   *  (백엔드 proto/layout.py의 `SINGLE_ID`) 제목으로 쓸 수 없다 — 구별할 대상이
   *  하나뿐이어서 슬러그가 될 것이 없다는 그 판단의 결과이고, 그래서 이름은
   *  본문에서만 온다. */
  name: string | null;
  spec_path: string;
  state: PrototypeState;
  port: number | null;
  /** 지금 빌드 세션이 열려 있는가.
   *
   *  **`state`와 별개인 이유.** 서버가 떠 있는지와 세션이 열려 있는지는 서로
   *  독립적인 사실이다. 열거형 하나가 둘을 실어 나르던 동안 세션이 호스팅을
   *  가려서, 실행 중인 프로토타입을 수정하기 시작하면 카드가 `building`이 되어
   *  프리뷰·공유 링크가 사라졌다 — 서버는 계속 떠 있었으므로 화면만 거짓말을
   *  했다. 이제 `state`는 산출물과 서버만 말하고, 세션은 이 값이 말한다.
   *
   *  카드의 빌드 버튼이 이 값으로 갈린다: 열려 있으면 "세션 열기", 아니면
   *  상태에 맞는 "수정하기"/"이어서 하기". */
  session_open: boolean;
  /** 떠 있는 서버가 소스보다 오래됐는가 — 즉 프리뷰와 참가자 링크가 **이전
   *  버전**을 보여주고 있는가.
   *
   *  **`session_open`으로 대신할 수 없다.** 세션이 닫히는 순간 그 신호는
   *  사라지는데, 정작 그때가 사용자가 "수정이 반영됐다"고 오해하기 가장 쉬운
   *  시점이다. 서버가 소스를 읽은 시각(`HostInfo.built_at`)과 빌드 트리의 최신
   *  소스 mtime을 서버가 비교해 내려보낸다.
   *
   *  `running`이 아니면 항상 false다 — 낡을 프리뷰가 없다. */
  preview_stale: boolean;
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

// Opens the prototype build stream for one turn: POST the text (starts the turn
// as a server job — backend aipds/turn_job.py), then watch it by id. Text rides
// in the POST body, not the URL: a long Korean message becomes a
// ~9-byte-per-char query string that pushed the request line past Node's 16KB
// maxHeaderSize and came back as HTTP 431 (lib/api/sse.ts's openViaHandle
// documents the measurement).
//
// 자동 개시(`__first__` 센티넬)도 같은 길로 간다 — 턴 id를 받아야 끊겼을 때 같은
// 턴에 다시 붙을 수 있다. 서버가 센티넬을 session.first_prompt()로 바꾼다
// (backend routes/prototypes.py의 _opening_text).
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

/** 빌드 턴 하나를 `after` 뒤부터 본다 — 다시 붙기(끊김, 새로고침)와 재생에 쓴다. */
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
  /** 사용자가 보낸 말. 자동 개시 턴은 null. */
  input: string | null;
}

/** 열린 빌드 세션과 그 턴들. 세션이 없으면 null. 빌드 화면이 다시 열릴 때 이것으로
 *  대화를 처음부터 재생하고 도는 턴에 붙는다. */
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
