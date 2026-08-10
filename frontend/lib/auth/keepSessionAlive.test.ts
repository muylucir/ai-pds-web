import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { keepSessionAlive, REFRESH_INTERVAL_MS } from "./keepSessionAlive";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** POST /api/auth/refresh의 성공/실패 응답을 흉내내는 fetch 스텁. */
function stubRefresh(ok: boolean) {
  // 인수를 명시적으로 받는다 — 인수 없는 () => ... 로 두면 mock.calls의 튜플
  // 타입이 빈 배열로 추론되어 calls[0][0]/[1]을 읽는 단정이 타입 오류가 된다.
  return vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
    new Response(JSON.stringify({ authenticated: ok }),
                 { status: ok ? 200 : 401 }));
}

describe("keepSessionAlive", () => {
  it("refreshes on the interval so a long stream never outlives its access token", async () => {
    // 이것이 이 모듈의 존재 이유다: 프로토타입 빌드는 한 번의 SSE 연결로 수십
    // 분을 사는데, /api 프록시의 갱신은 401을 받아야 발동하므로 그 사이에
    // 갱신 기회가 하나도 없다(app/api/auth/refresh/route.ts 참조).
    const fetchSpy = stubRefresh(true);
    vi.stubGlobal("fetch", fetchSpy);

    keepSessionAlive();

    // 개시 직후에는 부르지 않는다 — 방금 로그인/네비게이션으로 받은 토큰이
    // 이미 신선하고, 화면 진입마다 Cognito를 때릴 이유가 없다.
    expect(fetchSpy).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/auth/refresh");
    expect(fetchSpy.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });

    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  // access 토큰은 60분이다. 그보다 넉넉히 앞서 갱신해야 만료 창이 생기지
  // 않는다 — 간격이 60분에 가까우면 지연/일시적 실패 한 번에 만료된다.
  it("refreshes well inside the 60-minute access-token lifetime", () => {
    expect(REFRESH_INTERVAL_MS).toBeLessThanOrEqual(30 * 60_000);
    // 너무 짧으면 폴링이 Cognito를 불필요하게 때린다.
    expect(REFRESH_INTERVAL_MS).toBeGreaterThanOrEqual(5 * 60_000);
  });

  it("stops polling once the refresh token is gone (401)", async () => {
    // 401은 30일 창이 끝났다는 뜻이다. 계속 폴링하면 매 간격마다 Cognito를
    // 때리며 401을 반복해서 받는다 — 그 사용자는 이미 로그인해야 한다.
    const fetchSpy = stubRefresh(false);
    vi.stubGlobal("fetch", fetchSpy);

    keepSessionAlive();
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS * 3);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("keeps polling when a refresh fails for a transient reason", async () => {
    // 네트워크가 잠깐 끊긴 것과 세션 종료를 구분한다. 여기서 멈추면 와이파이가
    // 한 번 끊긴 사용자가 빌드 도중 조용히 세션을 잃는다.
    const fetchSpy = vi.fn(async () => { throw new TypeError("network down"); });
    vi.stubGlobal("fetch", fetchSpy);

    keepSessionAlive();
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("returns a stop function that cancels the timer", async () => {
    const fetchSpy = stubRefresh(true);
    vi.stubGlobal("fetch", fetchSpy);

    const stop = keepSessionAlive();
    stop();
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS * 2);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
