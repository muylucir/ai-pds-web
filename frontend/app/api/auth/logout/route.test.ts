import { beforeEach, describe, expect, it, vi } from "vitest";

// A route handler must read process.env at call time, not at module load time
// (this is why cognitoEnv() is a function). The test sets the env first.
beforeEach(() => {
  process.env.COGNITO_HOSTED_UI_DOMAIN = "pool.auth.ap-northeast-2.amazoncognito.com";
  process.env.COGNITO_CLIENT_ID = "client-abc";
  process.env.COGNITO_CLIENT_SECRET = "secret-xyz";
  process.env.APP_BASE_URL = "https://app.example.com";
  vi.restoreAllMocks();
});

function request(method: "GET" | "POST"): Request {
  return new Request("https://app.example.com/api/auth/logout", { method });
}

/** Does the browser change the method when it follows this redirect?
 *
 * 303 See Other and 302 Found follow a POST as a GET. 307/308
 * **preserve the method** — that is the core of the invariant this file guards. */
const METHOD_PRESERVING = [307, 308];

describe("POST /api/auth/logout", () => {
  // Observed defect (2026-08-04): clicking the logout button in the header showed, in the browser console,
  //   POST https://<pool>.auth.<region>.amazoncognito.com/logout?... 405 (Method Not Allowed)
  // and the user was not logged out.
  //
  // The cause is that the **default status of NextResponse.redirect() is 307**
  // (next/dist/server/web/spec-extension/response.js: `?? 307`). By spec, 307
  // preserves the method, so the button's POST is reissued as a POST to the Cognito Hosted UI's
  // /logout. That endpoint accepts only GET, hence the 405.
  //
  // Our response is neither a 200 nor an error (a normal 307), so nothing shows up in the
  // server log — it is a failure visible only in the browser console.
  it("redirects to the Hosted UI with a status that turns the POST into a GET", async () => {
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    expect(METHOD_PRESERVING).not.toContain(res.status);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toContain("/logout?");
  });

  it("still sends the user to the Hosted UI logout with our client_id and logout_uri", async () => {
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    const location = res.headers.get("location") ?? "";
    expect(location).toContain("pool.auth.ap-northeast-2.amazoncognito.com/logout");
    // logout_uri must match a value registered on the Cognito app client exactly.
    expect(location).toContain("client_id=client-abc");
    expect(location).toContain(encodeURIComponent("https://app.example.com/login"));
  });

  it("clears all three auth cookies on the way out", async () => {
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    // If the cookies are not cleared, the gate still passes after returning from the Hosted UI.
    const setCookie = res.headers.getSetCookie().join("\n");
    for (const name of ["aipds_access", "aipds_id", "aipds_refresh"]) {
      expect(setCookie).toContain(`${name}=`);
    }
    expect(setCookie).toContain("Max-Age=0");
  });

  it("falls back to /login when Cognito is not configured (local bypass)", async () => {
    process.env.COGNITO_HOSTED_UI_DOMAIN = "";
    process.env.COGNITO_CLIENT_ID = "";
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    // For the same reason, this path must not preserve the method either — /login is a page (GET).
    expect(METHOD_PRESERVING).not.toContain(res.status);
    expect(res.headers.get("location")).toBe("/login");
  });
});

describe("GET /api/auth/logout", () => {
  it("works too, for a plain link or a direct visit", async () => {
    const { GET } = await import("./route");
    const res = await GET(request("GET") as never);

    expect(res.headers.get("location")).toContain("/logout?");
    // GET has no method-preservation problem, but both entry points returning the same response
    // is this route's contract (POST calls GET).
    expect(res.status).toBe(303);
  });
});
