// frontend/lib/api/sse.ts
import { API_BASE_URL, apiErrorFrom } from "./client";
import { CREDENTIALS } from "@/lib/auth";
import type { AgentEvent } from "./types";

export interface StreamHandlers {
  /** `seq` is the frame's SSE `id` — its position in the turn log. When reattaching, pass
   *  the last value received as `watchTurn`'s `after` (turn_response in backend routes/turns.py). */
  onEvent: (ev: AgentEvent, seq?: number) => void;
  onDone: () => void;
  onError?: (err: unknown) => void;
  /** Body of the turn-start response (POST). Called once, **before** the stream opens.
   *
   *  Answer submission uses this: the bubble text the server built (`summary`) arrives
   *  here (backend routes/answers.py). The point is that the frontend does not rebuild that
   *  text — with two renderers, the screen and the transcript diverge, and they actually did
   *  (backend/aipds/answer_summary.py header). Every handle path receives this callback, but
   *  only answer submission uses it, so it is optional. */
  onCreated?: (payload: TurnCreated) => void;
}

/** Turn-start response. Fields other than `turn_id` vary by path. */
export interface TurnCreated {
  turn_id?: string;
  /** Answer-submission path: the user bubble text built by the server. */
  summary?: string;
}

// Shared EventSource plumbing: parses each frame's `data` as a JSON-encoded
// AgentEvent (matches backend turns.py), finishes on a "done"/"error" event or
// a transport error, and closes the EventSource. Returns an unsubscribe
// function for React effect cleanup.
//
// Exported so prototypes.ts's build stream reuses this one implementation.
// It used to keep a hand-copied twin of this ~30-line shape because the helper
// was private — two copies of the frame contract is one too many.
export function openStream(url: string, handlers: StreamHandlers): () => void {
  const es = new EventSource(url);

  const close = () => es.close();

  es.onmessage = (ev: MessageEvent) => {
    let parsed: AgentEvent;
    try {
      parsed = JSON.parse(ev.data) as AgentEvent;
    } catch (err) {
      handlers.onError?.(err);
      return;
    }
    const seq = ev.lastEventId ? Number(ev.lastEventId) : undefined;
    handlers.onEvent(parsed, Number.isFinite(seq) ? seq : undefined);
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

/**
 * Sends the turn input **in the body** to start a turn, and receives its id.
 *
 * **Why two steps.** Previously the text went in the SSE URL's query string
 * (`?text=...`), because EventSource supports only GET and cannot carry a body.
 * But encodeURIComponent turns each Korean character into 9 bytes. Measured:
 * 2,164-character input → 14,376-byte request line. Adding the auth cookies (3 Cognito
 * JWTs, about 3.7KB) pushed it past Node.js's default maxHeaderSize of 16,384 bytes,
 * and the Next.js proxy rejected it with **HTTP 431**.
 *
 * Why that failure showed on screen as "연결이 끊어졌습니다" (connection lost): EventSource
 * does not expose the HTTP status code. Whether 431, 500, or a network drop, only
 * onerror fires, the same way. So the cause was completely hidden from the screen.
 *
 * Why EventSource was not replaced with fetch+ReadableStream: this proxy layer has
 * already hit and solved SSE breaking under HTTP/2
 * (the ERR_HTTP2_PROTOCOL_ERROR record in the app/api/[...path]/route.ts header).
 * Keep EventSource, with reconnection and cookie auth built into the browser, and remove
 * only the URL length that caused the problem.
 *
 * **Start failures surface here.** POST returns a status code, so failures like 431/413
 * reach the caller as ApiError instead of being flattened into a stream error — that is
 * the safeguard that keeps this defect from hiding again.
 */
async function createTurn(path: string, body: unknown): Promise<TurnCreated> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: CREDENTIALS,
    body: JSON.stringify(body),
  });
  // On 409 `turn_in_progress`, the ApiError carries the id of the running turn (apiErrorFrom).
  if (!res.ok) throw await apiErrorFrom(res);
  // Return the whole body — while this extracted only `turn_id`, the bubble text the server
  // sent along was dropped here, so the frontend had to rebuild it.
  return (await res.json()) as TurnCreated;
}

/**
 * Common plumbing that starts a turn and opens a stream with its id.
 *
 * The returned unsubscribe is valid **even while the start request is still in flight**:
 * it sets a cancel flag so a stream is not opened with a late-arriving id. Without it,
 * an orphan stream is left behind when the user leaves the screen right away. Closing the
 * stream does not stop the turn on the server — closing only stops watching.
 */
export function openViaHandle(
  createPath: string,
  body: unknown,
  streamUrl: (turnId: string) => string,
  handlers: StreamHandlers,
): () => void {
  let cancelled = false;
  let closeStream: (() => void) | null = null;

  void createTurn(createPath, body)
    .then((created) => {
      if (cancelled) return;
      handlers.onCreated?.(created);
      closeStream = openStream(streamUrl(created.turn_id ?? ""), handlers);
    })
    .catch((err) => {
      if (cancelled) return;
      // The turn could not be opened — so no stream opens either. Call onDone as well so the
      // caller's "in progress" state is always released (otherwise input stays locked forever).
      handlers.onError?.(err);
      handlers.onDone();
    });

  return () => {
    cancelled = true;
    closeStream?.();
  };
}

/**
 * Watches one turn — `after=0` the first time, the last received seq when reattaching.
 *
 * A turn is a server job, not a request (backend aipds/turn_job.py). Even if the watching
 * screen disconnects, the turn keeps running, and reattaching with the same id resumes
 * from the missed events. Refresh, wake from sleep, and a second tab all take this path.
 *
 * Why a query parameter instead of the `Last-Event-ID` header: EventSource's automatic
 * reconnection is blocked because `openStream` closes on onerror (it has to distinguish a 401
 * from a disconnect). The caller reconnects explicitly and passes the position in the URL then.
 */
export function watchTurn(
  pid: string,
  turnId: string,
  after: number,
  handlers: StreamHandlers,
): () => void {
  const p = encodeURIComponent(pid);
  const t = encodeURIComponent(turnId);
  return openStream(
    `${API_BASE_URL}/projects/${p}/events?turn=${t}&after=${after}`, handlers);
}

// Opens the events stream for one turn: POST the text (starts the turn), then watch it by id.
export function streamEvents(pid: string, text: string, handlers: StreamHandlers): () => void {
  const p = encodeURIComponent(pid);
  return openViaHandle(
    `/projects/${p}/turns`,
    { text },
    (turnId) => `${API_BASE_URL}/projects/${p}/events?turn=${encodeURIComponent(turnId)}`,
    handlers,
  );
}

// Answer submission for a round that came from a question file.
//
// How this differs from streamAnswers: that one wakes the parked `can_use_tool` future and
// continues **the same CLI turn**. This round has no such future — the PostToolUse hook saw
// the question file and already ended the turn. So the backend writes the answers to the file,
// starts a **new turn**, and returns its id, and the screen watches it via `/events` just like
// a regular turn.
//
// The backend builds the sentence for the continuing turn (file_answers_recorded in
// agent/prompts.py): text the agent reads must follow the project language, not the UI language.
export function streamFileAnswers(
  pid: string,
  file: string,
  answers: Record<string, string>,
  handlers: StreamHandlers,
): () => void {
  const p = encodeURIComponent(pid);
  const path = file.split("/").map(encodeURIComponent).join("/");
  return openViaHandle(
    `/projects/${p}/questions/${path}/answers`,
    { answers },
    (turnId) => `${API_BASE_URL}/projects/${p}/events?turn=${encodeURIComponent(turnId)}`,
    handlers,
  );
}

// The answer-submission twin of streamEvents. Answers ride in the body for the
// same reason: a long free-text answer hits the same URL length ceiling.
export function streamAnswers(
  pid: string,
  answers: Record<string, string>,
  handlers: StreamHandlers,
): () => void {
  const p = encodeURIComponent(pid);
  return openViaHandle(
    `/projects/${p}/answers`,
    { answers },
    (turnId) => `${API_BASE_URL}/projects/${p}/events?turn=${encodeURIComponent(turnId)}`,
    handlers,
  );
}
