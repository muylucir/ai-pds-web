import { beforeEach, describe, expect, it, vi } from "vitest";

// The route handler must read process.env at call time, not module load time (which is why
// cognitoEnv() is a function). The test sets env first.
beforeEach(() => {
  process.env.COGNITO_HOSTED_UI_DOMAIN = "pool.auth.ap-northeast-2.amazoncognito.com";
  process.env.COGNITO_CLIENT_ID = "client-abc";
  process.env.COGNITO_CLIENT_SECRET = "secret-xyz";
  process.env.APP_BASE_URL = "https://app.example.com";
  vi.restoreAllMocks();
});

function request(url: string, cookies: Record<string, string> = {}) {
  const cookie = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`).join("; ");
  return new Request(url, { headers: cookie ? { cookie } : {} });
}

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

function mockTokenEndpoint() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({
      access_token: fakeJwt({ "cognito:groups": ["admin"], username: "a@example.com" }),
      id_token: fakeJwt({ email: "a@example.com" }),
      refresh_token: "rt",
      expires_in: 3600,
    }), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

describe("GET /api/auth/callback", () => {
  it("sets three httpOnly cookies and redirects to the saved next path", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { aipds_pkce: "verifier-1", aipds_state: "s1", aipds_next: "/projects/p1/dashboard" },
    ) as never);

    expect(res.status).toBe(302);
    expect(res.headers.get("location"))
      .toBe("/projects/p1/dashboard");

    const setCookies = res.headers.getSetCookie();
    const joined = setCookies.join("\n");
    for (const name of ["aipds_access", "aipds_id", "aipds_refresh"]) {
      expect(joined).toContain(`${name}=`);
    }
    // The token must not be exposed to JS — this is the core property of this design.
    for (const c of setCookies.filter((c) => /^aipds_(access|id|refresh)=/.test(c))) {
      expect(c).toMatch(/HttpOnly/i);
      expect(c).toMatch(/SameSite=Lax/i);
    }
    // The round-trip cookies are deleted after being consumed.
    expect(joined).toMatch(/aipds_pkce=;|aipds_pkce=""/);
    expect(joined).toMatch(/aipds_state=;|aipds_state=""/);
  });

  it("defaults to / when no next cookie was saved", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { aipds_pkce: "v", aipds_state: "s1" },
    ) as never);
    expect(res.headers.get("location")).toBe("/");
  });

  it("rejects a state mismatch without calling the token endpoint", async () => {
    // CSRF defense: a callback induced by an attacker does not match the state cookie we planted.
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=attacker",
      { aipds_pkce: "v", aipds_state: "ours" },
    ) as never);
    expect(res.status).toBe(302);
    expect(res.headers.get("location"))
      .toBe("/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("clears the round-trip cookies even on a state mismatch", async () => {
    // aipds_pkce/aipds_state/aipds_next are cleared on the failure path too — clearing them only on the
    // success path would leave a failed attempt's PKCE material in the browser until the next login.
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=attacker",
      { aipds_pkce: "v", aipds_state: "ours", aipds_next: "/somewhere" },
    ) as never);
    const joined = res.headers.getSetCookie().join("\n");
    expect(joined).toMatch(/aipds_pkce=;|aipds_pkce=""/);
    expect(joined).toMatch(/aipds_state=;|aipds_state=""/);
    expect(joined).toMatch(/aipds_next=;|aipds_next=""/);
  });

  it("rejects a missing verifier cookie", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { aipds_state: "s1" },
    ) as never);
    expect(res.headers.get("location"))
      .toBe("/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("surfaces a Hosted UI error without attempting an exchange", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?error=access_denied",
    ) as never);
    expect(res.headers.get("location"))
      .toBe("/login?error=access_denied");
    expect(f).not.toHaveBeenCalled();
  });

  it("redirects to /login when the token exchange fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=stale&state=s1",
      { aipds_pkce: "v", aipds_state: "s1" },
    ) as never);
    expect(res.headers.get("location"))
      .toBe("/login?error=exchange_failed");
  });

  it("refuses an off-site next path", async () => {
    // Open redirect defense: next must be a path inside our site.
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { aipds_pkce: "v", aipds_state: "s1", aipds_next: "https://evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("/");
  });

  it("refuses a protocol-relative next path", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { aipds_pkce: "v", aipds_state: "s1", aipds_next: "//evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("/");
  });
});

describe("GET /api/auth/callback — Location은 오리진을 새지 않는다", () => {
  // Measured deployment bug: behind CloudFront, Location came out as https://localhost:3000/....
  // Next 15 assembles req.url from the server's own origin rather than the Host header, so
  // new URL(next, req.url) leaks the internal address behind a proxy.
  // With a relative Location, the browser resolves it against the current origin (CloudFront).
  it("redirects relatively after a successful exchange", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      // Reproduces the behind-a-proxy situation: a request that came in at the internal address.
      "http://localhost:3000/api/auth/callback?code=c1&state=s1",
      { aipds_pkce: "v", aipds_state: "s1", aipds_next: "/projects/p1/dashboard" },
    ) as never);
    const location = res.headers.get("location")!;
    expect(location).toBe("/projects/p1/dashboard");
    expect(location).not.toContain("localhost");
    expect(location).not.toMatch(/^https?:\/\//);
  });

  it("redirects relatively on failure too", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "http://localhost:3000/api/auth/callback?code=c1&state=attacker",
      { aipds_pkce: "v", aipds_state: "ours" },
    ) as never);
    const location = res.headers.get("location")!;
    expect(location).toBe("/login?error=state_mismatch");
    expect(location).not.toContain("localhost");
  });

  it("still refuses an off-site next cookie (open redirect)", async () => {
    // Whether the safeNext defense loosened when switching to a relative Location —
    // "//evil.example" looks like a relative value, but the browser reads it as off-site.
    const { GET } = await import("./route");
    for (const evil of ["https://evil.example/x", "//evil.example", "/\\evil.example"]) {
      // Set up the mock fresh on every iteration — setting it up only once makes the second exchange fail
      // and fall into exchange_failed, giving the false impression that the defense worked.
      mockTokenEndpoint();
      const res = await GET(request(
        "https://app.example.com/api/auth/callback?code=c1&state=s1",
        { aipds_pkce: "v", aipds_state: "s1", aipds_next: evil },
      ) as never);
      const location = res.headers.get("location")!;
      expect(location, `next=${evil}`).toBe("/");
      expect(location).not.toContain("evil.example");
    }
  });
});
