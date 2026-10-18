// Fix for the defect where the session expires during a prototype build.
//
// **Why this route is needed.** Token refresh fires only when the /api proxy
// **receives a 401** from the backend (isRetryableWithRefresh in
// app/api/[...path]/route.ts). A prototype build never meets that condition:
//
//   1. POST /turns        — valid token, 200
//   2. GET  /events?turn= — valid token, 200. SSE connection established
//   3. For the next tens of minutes only events flow. The connection already got
//      its 200, so no 401 will ever come -- there is no way to enter the refresh path
//   4. The access token (60 min) expires in the meantime, and the first request
//      after the build ends gets 401 → /login
//
// I.e. it expires not because the stream drops, but **because there is no chance
// to refresh while the stream runs**. It shows up less in Discovery chat because
// turns are short and every turn is a chance to refresh.
//
// So refresh is split into an **explicit path** that does not depend on a request
// failing. The client calls it periodically, independent of the stream
// (lib/auth/keepSessionAlive.ts).
//
// /api/auth/me cannot substitute: that route only checks that the cookie
// **exists**, does not refresh, and does not go through the /api proxy.
import { beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  process.env.COGNITO_HOSTED_UI_DOMAIN = "pool.auth.ap-northeast-2.amazoncognito.com";
  process.env.COGNITO_CLIENT_ID = "client-abc";
  process.env.COGNITO_CLIENT_SECRET = "secret-xyz";
  process.env.APP_BASE_URL = "https://app.example.com";
  vi.restoreAllMocks();
  vi.resetModules();
});

function request(cookie?: string): Request {
  return new Request("https://app.example.com/api/auth/refresh", {
    method: "POST",
    headers: cookie ? { cookie } : {},
  });
}

/** A fetch stub imitating a successful Cognito /oauth2/token response. */
function stubTokenEndpoint(body: Record<string, unknown>, status = 200) {
  // Take the arguments explicitly -- with an argument-less () => ..., the tuple
  // type of mock.calls is inferred as an empty array, and the assertion reading
  // calls[0][1].body becomes a type error.
  return vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
    new Response(JSON.stringify(body), {
      status, headers: { "content-type": "application/json" },
    }));
}

describe("POST /api/auth/refresh", () => {
  it("exchanges the refresh cookie for a new access token and sets it", async () => {
    const fetchSpy = stubTokenEndpoint({
      access_token: "new-access", id_token: "new-id", expires_in: 3600,
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await import("./route");
    const res = await POST(request("aipds_refresh=r-1") as never);

    expect(res.status).toBe(200);
    const setCookie = res.headers.getSetCookie().join("\n");
    expect(setCookie).toContain("aipds_access=new-access");
    expect(setCookie).toContain("aipds_id=new-id");
    // Without httpOnly, the refreshed token alone becomes readable from JS, so
    // this route would be creating a new XSS exposure surface.
    expect(setCookie).toContain("HttpOnly");
  });

  it("uses the refresh_token grant with the cookie's token", async () => {
    const fetchSpy = stubTokenEndpoint({
      access_token: "a", id_token: "i", expires_in: 3600,
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await import("./route");
    await POST(request("aipds_refresh=r-1") as never);

    const body = String(fetchSpy.mock.calls[0]?.[1]?.body ?? "");
    expect(body).toContain("grant_type=refresh_token");
    expect(body).toContain("refresh_token=r-1");
  });

  it("401s without calling Cognito when there is no refresh cookie", async () => {
    const fetchSpy = stubTokenEndpoint({});
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await import("./route");
    const res = await POST(request() as never);

    expect(res.status).toBe(401);
    // Without a refresh token there is nothing to call -- do not hit Cognito.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("401s when the refresh token has expired or been revoked", async () => {
    // 30 days have passed or it was revoked -- answering 200 here makes the client
    // wrongly conclude the session is alive and keep polling, and the user is only
    // logged out on the next real request.
    vi.stubGlobal("fetch", stubTokenEndpoint({ error: "invalid_grant" }, 400));

    const { POST } = await import("./route");
    const res = await POST(request("aipds_refresh=stale") as never);

    expect(res.status).toBe(401);
    await expect(res.json()).resolves.toMatchObject({ authenticated: false });
  });

  it("does not clear the refresh cookie on a successful refresh", async () => {
    // This pool has refresh rotation off (refreshTokenRotationGracePeriod unset in
    // infra/lib/auth-client-config.ts), so the refresh grant returns no new
    // refresh_token. Touching the existing cookie would wipe out the whole 30-day window.
    vi.stubGlobal("fetch", stubTokenEndpoint({
      access_token: "a", id_token: "i", expires_in: 3600,
    }));

    const { POST } = await import("./route");
    const res = await POST(request("aipds_refresh=r-1") as never);

    const setCookie = res.headers.getSetCookie().join("\n");
    expect(setCookie).not.toContain("aipds_refresh=");
  });
});
