// frontend/lib/api/sse.ts
import { API_BASE_URL, apiErrorFrom } from "./client";
import { CREDENTIALS } from "@/lib/auth";
import type { AgentEvent } from "./types";

export interface StreamHandlers {
  /** `seq`는 프레임의 SSE `id` — 턴 로그 안의 위치다. 다시 붙을 때 마지막으로 받은
   *  값을 `watchTurn`의 `after`로 준다(backend routes/turns.py의 turn_response). */
  onEvent: (ev: AgentEvent, seq?: number) => void;
  onDone: () => void;
  onError?: (err: unknown) => void;
  /** 턴 개시 응답(POST)의 본문. 스트림이 열리기 **전에** 한 번 불린다.
   *
   *  답변 제출이 이것을 쓴다: 서버가 만든 말풍선 텍스트(`summary`)가 여기 실려
   *  온다(backend routes/answers.py). 프론트가 그 텍스트를 다시 만들지 않는 것이
   *  요점이다 — 렌더가 두 벌이면 화면과 트랜스크립트가 갈라지고, 실제로 갈라졌다
   *  (backend/aipds/answer_summary.py 헤더). 핸들 경로 전부가 이 콜백을 받지만
   *  쓰는 곳은 답변 제출뿐이므로 옵셔널이다. */
  onCreated?: (payload: TurnCreated) => void;
}

/** 턴 개시 응답. `turn_id` 외의 필드는 경로별로 다르다. */
export interface TurnCreated {
  turn_id?: string;
  /** 답변 제출 경로: 서버가 만든 사용자 말풍선 텍스트. */
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
 * 턴 입력을 **본문으로** 보내 턴을 시작하고, 그 id를 받는다.
 *
 * **왜 2단계인가.** 종전에는 텍스트가 SSE URL의 쿼리스트링으로 갔다
 * (`?text=...`). EventSource는 GET만 지원해 본문을 실을 수 없기 때문이다.
 * 그런데 한글은 encodeURIComponent로 한 글자가 9바이트가 된다. 실측:
 * 2,164자 입력 → 14,376바이트 요청 라인. 여기에 인증 쿠키(Cognito JWT 3개,
 * 약 3.7KB)가 더해져 Node.js의 maxHeaderSize 기본값 16,384바이트를 넘고,
 * Next.js 프록시가 **HTTP 431**로 거절했다.
 *
 * 그 실패가 화면에서 "연결이 끊어졌습니다"로 보인 이유: EventSource는 HTTP
 * 상태 코드를 노출하지 않는다. 431이든 500이든 네트워크 단절이든 똑같이
 * onerror만 발화한다. 그래서 원인이 화면에서 완전히 숨었다.
 *
 * EventSource를 fetch+ReadableStream으로 바꾸지 않은 이유: 이 프록시 계층은
 * HTTP/2에서 SSE가 깨지는 문제를 이미 겪고 해결한 곳이다
 * (app/api/[...path]/route.ts 헤더의 ERR_HTTP2_PROTOCOL_ERROR 기록).
 * 재연결과 쿠키 인증이 브라우저에 내장된 EventSource를 유지하고, 문제의
 * 원인인 URL 길이만 없앤다.
 *
 * **개시 실패는 여기서 드러난다.** POST는 상태 코드를 주므로, 431/413 같은
 * 실패가 스트림 오류로 뭉개지지 않고 ApiError로 호출부에 닿는다 — 그것이
 * 이 결함이 다시 숨지 않게 하는 장치다.
 */
async function createTurn(path: string, body: unknown): Promise<TurnCreated> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: CREDENTIALS,
    body: JSON.stringify(body),
  });
  // 409 `turn_in_progress`면 ApiError가 도는 턴의 id를 싣는다(apiErrorFrom).
  if (!res.ok) throw await apiErrorFrom(res);
  // 본문 전체를 돌려준다 — `turn_id`만 뽑던 동안은 서버가 함께 보낸 말풍선
  // 텍스트가 여기서 버려졌고, 그래서 프론트가 그것을 다시 만들어야 했다.
  return (await res.json()) as TurnCreated;
}

/**
 * 턴을 시작하고 그 id로 스트림을 여는 공통 배관.
 *
 * 반환된 unsubscribe는 **개시 요청이 아직 진행 중일 때도** 유효하다: 취소
 * 플래그를 세워 뒤늦게 도착한 id로 스트림을 열지 않는다. 없으면 사용자가
 * 곧바로 화면을 떠난 경우 고아 스트림이 남는다. 스트림을 닫아도 턴은 서버에서
 * 계속 돈다 — 닫는 것은 보는 것뿐이다.
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
      // 턴을 열지 못했다 — 스트림도 열리지 않는다. onDone까지 불러 호출부의
      // "진행 중" 상태를 반드시 풀어 준다(안 하면 입력이 영구히 잠긴다).
      handlers.onError?.(err);
      handlers.onDone();
    });

  return () => {
    cancelled = true;
    closeStream?.();
  };
}

/**
 * 턴 하나를 본다 — 처음이면 `after=0`, 다시 붙을 때는 마지막으로 받은 seq.
 *
 * 턴은 요청이 아니라 서버 작업이다(backend aipds/turn_job.py). 보는 화면이 끊겨도
 * 턴은 계속 돌고, 같은 id로 다시 붙으면 놓친 이벤트부터 이어서 받는다. 새로고침,
 * 절전 복귀, 두 번째 탭이 모두 이 경로다.
 *
 * `Last-Event-ID` 헤더가 아니라 쿼리를 쓰는 이유: EventSource의 자동 재연결은
 * `openStream`이 onerror에서 닫아 막는다(401과 끊김을 구별해야 한다). 재연결은
 * 호출부가 명시적으로 하고, 그때 위치를 URL로 준다.
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

// 질문 파일에서 온 라운드의 답변 제출.
//
// streamAnswers와 무엇이 다른가: 저쪽은 파킹된 `can_use_tool` future를 깨워 **같은
// CLI 턴**을 이어간다. 이 라운드에는 그 future가 없다 — PostToolUse 훅이 질문 파일을
// 보고 턴을 이미 끝냈다. 그래서 백엔드가 답변을 파일에 쓰고 **새 턴**을 시작해 그
// id를 주고, 화면은 보통 턴과 똑같이 `/events`로 본다.
//
// 이어갈 턴의 문장은 백엔드가 만든다(agent/prompts.py의 file_answers_recorded):
// 에이전트가 읽는 텍스트는 UI 언어가 아니라 프로젝트 언어를 따라야 한다.
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
