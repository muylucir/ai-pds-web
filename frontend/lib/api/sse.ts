// frontend/lib/api/sse.ts
import { API_BASE_URL } from "./client";
import type { AgentEvent } from "./types";

export interface StreamHandlers {
  onEvent: (ev: AgentEvent) => void;
  onDone: () => void;
  onError?: (err: unknown) => void;
}

// Shared EventSource plumbing for both SSE endpoints below: parses each
// frame's `data` as a JSON-encoded AgentEvent (matches backend turns.py),
// finishes on a "done"/"error" event or a transport error, and closes the
// EventSource. Returns an unsubscribe function for React effect cleanup.
function openStream(url: string, handlers: StreamHandlers): () => void {
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

// Opens GET /projects/{pid}/events?text=... as an SSE stream.
//
// NOTE: In this slice SSE has no in-scope consumer — document-review uses the
// synchronous postMessage path (see Task 4 Interfaces). This helper exists for
// the Conversational Canvas plan (out of scope here) and as a future upgrade
// path for long doc revisions.
export function streamEvents(pid: string, text: string, handlers: StreamHandlers): () => void {
  return openStream(
    `${API_BASE_URL}/projects/${encodeURIComponent(pid)}/events?text=${encodeURIComponent(text)}`,
    handlers,
  );
}

// Opens GET /projects/{pid}/answers/stream?answers=... as an SSE stream —
// the answer-submission twin of streamEvents (Task 9's /answers/stream).
export function streamAnswers(
  pid: string,
  answers: Record<string, string>,
  handlers: StreamHandlers,
): () => void {
  return openStream(
    `${API_BASE_URL}/projects/${encodeURIComponent(pid)}/answers/stream?answers=${encodeURIComponent(
      JSON.stringify(answers),
    )}`,
    handlers,
  );
}
