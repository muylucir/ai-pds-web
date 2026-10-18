import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { keepSessionAlive, REFRESH_INTERVAL_MS } from "./keepSessionAlive";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** A fetch stub mimicking success/failure responses of POST /api/auth/refresh. */
function stubRefresh(ok: boolean) {
  // Take the arguments explicitly — with an argument-less () => ..., the mock.calls tuple
  // type is inferred as an empty array and the assertions reading calls[0][0]/[1] become type errors.
  return vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
    new Response(JSON.stringify({ authenticated: ok }),
                 { status: ok ? 200 : 401 }));
}

describe("keepSessionAlive", () => {
  it("refreshes on the interval so a long stream never outlives its access token", async () => {
    // This is why this module exists: a prototype build lives for tens of minutes on a
    // single SSE connection, but the /api proxy's refresh fires only on a 401, so there is
    // no chance to refresh in between (see app/api/auth/refresh/route.ts).
    const fetchSpy = stubRefresh(true);
    vi.stubGlobal("fetch", fetchSpy);

    keepSessionAlive();

    // Don't call right after start — the token just received from login/navigation is
    // already fresh, and there is no reason to hit Cognito on every screen entry.
    expect(fetchSpy).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/auth/refresh");
    expect(fetchSpy.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });

    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  // The access token lasts 60 minutes. Refresh comfortably ahead of that so no expiry window
  // opens — with an interval close to 60 minutes, a single delay or transient failure lets it expire.
  it("refreshes well inside the 60-minute access-token lifetime", () => {
    expect(REFRESH_INTERVAL_MS).toBeLessThanOrEqual(30 * 60_000);
    // Too short and polling hits Cognito needlessly.
    expect(REFRESH_INTERVAL_MS).toBeGreaterThanOrEqual(5 * 60_000);
  });

  it("stops polling once the refresh token is gone (401)", async () => {
    // A 401 means the 30-day window has ended. Continuing to poll would hit Cognito at every
    // interval and keep getting 401 — that user already has to log in.
    const fetchSpy = stubRefresh(false);
    vi.stubGlobal("fetch", fetchSpy);

    keepSessionAlive();
    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS * 3);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("keeps polling when a refresh fails for a transient reason", async () => {
    // Distinguish a brief network drop from a session ending. Stopping here would make a user
    // whose Wi-Fi dropped once silently lose their session mid-build.
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
