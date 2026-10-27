import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import type { TokenSet } from "@/lib/auth/tokenExchange";

// cookies() and refreshTokens are replaced with mocks so route.ts can run without a real Cognito/Next
// runtime. proxyAuth.test.ts verified only the pure helpers, so this
// file verifies that those helpers are actually combined correctly inside proxy() — especially the 401
// refresh-retry-Set-Cookie path and SSE re-streaming.
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/lib/auth/tokenExchange", () => ({ refreshTokens: vi.fn() }));

import { cookies } from "next/headers";
import { refreshTokens } from "@/lib/auth/tokenExchange";

// Same reason as the callback route's test file: cognitoEnv() reads process.env at call time,
// so the test sets it first. refreshTokens is mocked and does not
// actually use this env, but we keep the convention and also confirm that cognitoEnv() does not
// throw.
beforeEach(() => {
  process.env.COGNITO_HOSTED_UI_DOMAIN = "pool.auth.ap-northeast-2.amazoncognito.com";
  process.env.COGNITO_CLIENT_ID = "client-abc";
  process.env.COGNITO_CLIENT_SECRET = "secret-xyz";
  process.env.APP_BASE_URL = "https://app.example.com";
  vi.restoreAllMocks();
});

function makeJar(map: Record<string, string>) {
  return {
    get: (name: string) => (name in map ? { name, value: map[name] } : undefined),
  };
}

function ctx(path: string[]) {
  return { params: Promise.resolve({ path }) };
}

function sentHeaders(call: unknown[]): Headers {
  const init = call[1] as RequestInit;
  return init.headers as Headers;
}

function sseStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]));
        i++;
      } else {
        controller.close();
      }
    },
  });
}

async function readAllChunks(body: ReadableStream<Uint8Array> | null): Promise<string[]> {
  if (!body) return [];
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const got: string[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    got.push(decoder.decode(value));
  }
  return got;
}

const REFRESHED_TOKENS: TokenSet = {
  access_token: "new-access", id_token: "new-id", expires_in: 3600,
};

describe("GET/POST/DELETE /api/[...path]", () => {
  // A persona demo keeps its login in its own cookies; before this, the proxy dropped them and
  // every screen after login bounced back to persona selection (workshop report, 2026-10-27).
  it("prototype preview: carries the prototype's own cookies, still never the app's", async () => {
    vi.mocked(cookies).mockResolvedValue(makeJar({ aipds_access: "tok-1" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("ok", { status: 200 }));

    const { GET } = await import("./route");
    const req = new NextRequest("https://preview.example.com/api/proto/ws-p/prototype/hq/evidence/", {
      method: "GET",
      headers: { cookie: "aipds_access=tok-1; aipds_proto_ab=t; sp_session=hq-1; aipds_refresh=r" },
    });
    await GET(req, ctx(["proto", "ws-p", "prototype", "hq", "evidence"]));

    expect(sentHeaders(fetchMock.mock.calls[0]).get("cookie")).toBe("aipds_proto_ab=t; sp_session=hq-1");
  });

  it("app API: a non-app cookie is still not forwarded", async () => {
    vi.mocked(cookies).mockResolvedValue(makeJar({ aipds_access: "tok-1" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 200 }));

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", {
      method: "GET", headers: { cookie: "aipds_access=tok-1; sp_session=hq-1" },
    });
    await GET(req, ctx(["projects"]));

    expect(sentHeaders(fetchMock.mock.calls[0]).get("cookie")).toBeNull();
  });

  it("passes every Set-Cookie of a response through separately", async () => {
    vi.mocked(cookies).mockResolvedValue(makeJar({}) as never);
    const upstream = new Headers();
    upstream.append("set-cookie", "sp_session=hq-1; Path=/api/proto/ws-p/prototype; HttpOnly");
    upstream.append("set-cookie", "sp_role=; Path=/api/proto/ws-p/prototype; Expires=Thu, 01 Jan 1970 00:00:00 GMT");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("ok", { status: 200, headers: upstream }));

    const { GET } = await import("./route");
    const res = await GET(new NextRequest("https://preview.example.com/api/proto/ws-p/prototype/login/"),
                          ctx(["proto", "ws-p", "prototype", "login"]));

    expect(res.headers.getSetCookie()).toEqual([
      "sp_session=hq-1; Path=/api/proto/ws-p/prototype; HttpOnly",
      "sp_role=; Path=/api/proto/ws-p/prototype; Expires=Thu, 01 Jan 1970 00:00:00 GMT",
    ]);
  });

  it("happy path: injects Bearer from the access cookie, strips Cookie, passes status/body through", async () => {
    vi.mocked(cookies).mockResolvedValue(makeJar({ aipds_access: "tok-1" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "content-type": "application/json" },
      }));

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", {
      method: "GET",
      headers: { cookie: "aipds_access=tok-1" },
    });
    const res = await GET(req, ctx(["projects"]));

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const headers = sentHeaders(fetchMock.mock.calls[0]);
    expect(headers.get("authorization")).toBe("Bearer tok-1");
    expect(headers.get("cookie")).toBeNull();
  });

  it("401 on a GET: refreshes once, retries with the new token, and writes Set-Cookie for access/id only", async () => {
    vi.mocked(cookies).mockResolvedValue(
      makeJar({ aipds_access: "old-tok", aipds_refresh: "ref-tok" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "content-type": "application/json" },
      }));
    vi.mocked(refreshTokens).mockResolvedValue(REFRESHED_TOKENS);

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", { method: "GET" });
    const res = await GET(req, ctx(["projects"]));

    expect(vi.mocked(refreshTokens)).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retryHeaders = sentHeaders(fetchMock.mock.calls[1]);
    expect(retryHeaders.get("authorization")).toBe("Bearer new-access");

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });

    const setCookies = res.headers.getSetCookie();
    const joined = setCookies.join("\n");
    expect(joined).toContain("aipds_access=new-access");
    expect(joined).toContain("aipds_id=new-id");
    expect(joined).not.toContain("aipds_refresh="); // the refresh token itself is not reissued, so it is not rewritten
    for (const c of setCookies) {
      expect(c).toMatch(/Path=\//);
      expect(c).toMatch(/Max-Age=3600/);
      expect(c).toMatch(/HttpOnly/i);
      expect(c).toMatch(/SameSite=Lax/i);
    }
  });

  it("401 on a GET: refreshTokens throws (expired/no Cognito env) — original 401 passes through, no Set-Cookie", async () => {
    vi.mocked(cookies).mockResolvedValue(
      makeJar({ aipds_access: "old-tok", aipds_refresh: "ref-tok" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }));
    vi.mocked(refreshTokens).mockRejectedValue(new Error("token endpoint rejected the request"));
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", { method: "GET" });
    const res = await GET(req, ctx(["projects"]));

    expect(res.status).toBe(401);
    // From the result alone, "tried to refresh and failed" and "never even tried" cannot be
    // told apart — both look like 1 fetch, a 401, and no Set-Cookie. Only by directly checking
    // whether refreshTokens was called can we catch the regression where "the retry itself was quietly
    // turned off".
    expect(vi.mocked(refreshTokens)).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1); // refresh itself failed — no retry fetch
    expect(res.headers.getSetCookie()).toHaveLength(0);
    errSpy.mockRestore();
  });

  it("401 on a POST: not retried — a streamed request body cannot be replayed", async () => {
    vi.mocked(cookies).mockResolvedValue(
      makeJar({ aipds_access: "tok-1", aipds_refresh: "ref-tok" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }));

    const { POST } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: "x" }),
    });
    const res = await POST(req, ctx(["projects"]));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.status).toBe(401);
    expect(vi.mocked(refreshTokens)).not.toHaveBeenCalled();
  });

  it("401 -> refresh -> retry: the retried SSE response is re-streamed, not buffered", async () => {
    vi.mocked(cookies).mockResolvedValue(
      makeJar({ aipds_access: "old-tok", aipds_refresh: "ref-tok" }) as never);
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(sseStream(["event: one\n\n", "event: two\n\n"]), {
        status: 200, headers: { "content-type": "text/event-stream" },
      }));
    vi.mocked(refreshTokens).mockResolvedValue(REFRESHED_TOKENS);

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/prototypes/x/events", { method: "GET" });
    const res = await GET(req, ctx(["prototypes", "x", "events"]));

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    const got = await readAllChunks(res.body);
    expect(got).toEqual(["event: one\n\n", "event: two\n\n"]);
  });

  it("no cookies at all: no Authorization header is sent; the backend response passes through unchanged", async () => {
    vi.mocked(cookies).mockResolvedValue(makeJar({}) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "content-type": "application/json" },
      }));

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", { method: "GET" });
    const res = await GET(req, ctx(["projects"]));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const headers = sentHeaders(fetchMock.mock.calls[0]);
    expect(headers.get("authorization")).toBeNull();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });
});

// Finding 1's routing fix merges nginx's /api/ and / locations onto Next, so
// EVERY method that used to reach FastAPI directly now transits this route
// handler — including PATCH/HEAD/OPTIONS, which backend/aipds/routes/
// proto_public.py's proxy_prototype route accepts for hosted prototype
// previews. If any of the seven aren't exported as named handlers, Next
// itself answers with a 405 (or an auto-generated 204 for a bare OPTIONS)
// before proxy() ever runs — silently narrowing what a previewed prototype
// can do. This test pins all seven so that regression can't creep back in.
describe("all seven methods proto_public.py accepts are exported and proxy through", () => {
  it.each(["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"] as const)(
    "%s is exported and forwards the same method to the backend",
    async (method) => {
      vi.mocked(cookies).mockResolvedValue(makeJar({}) as never);
      const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(null, { status: 200 }));

      const route = await import("./route");
      const handler = route[method];
      expect(handler, `expected route.ts to export ${method}`).toBeTypeOf("function");

      const req = new NextRequest("https://app.example.com/api/proto/p1/demo/", { method });
      await handler(req, ctx(["proto", "p1", "demo", ""]));

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const sentInit = fetchMock.mock.calls[0][1] as RequestInit;
      expect(sentInit.method).toBe(method);
    },
  );
});
