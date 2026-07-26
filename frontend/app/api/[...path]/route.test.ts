import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import type { TokenSet } from "@/lib/auth/tokenExchange";

// cookies()와 refreshTokens는 route.ts가 실제 Cognito/Next 런타임 없이 돌 수
// 있도록 목으로 대체한다. proxyAuth.test.ts는 순수 헬퍼만 검증했으므로, 이
// 파일은 그 헬퍼들이 실제로 proxy() 안에서 올바르게 조합되는지 — 특히 401
// 리프레시-재시도-Set-Cookie 경로와 SSE 재스트리밍 — 를 검증한다.
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/lib/auth/tokenExchange", () => ({ refreshTokens: vi.fn() }));

import { cookies } from "next/headers";
import { refreshTokens } from "@/lib/auth/tokenExchange";

// callback route의 테스트 파일과 같은 이유: cognitoEnv()는 호출 시점에
// process.env를 읽으므로 테스트가 먼저 세팅한다. refreshTokens는 모킹되어
// 실제로 이 env를 쓰지 않지만, 관례를 맞추고 cognitoEnv()가 던지지 않는다는
// 것도 확인해 둔다.
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
  it("happy path: injects Bearer from the access cookie, strips Cookie, passes status/body through", async () => {
    vi.mocked(cookies).mockResolvedValue(makeJar({ pf_access: "tok-1" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "content-type": "application/json" },
      }));

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", {
      method: "GET",
      headers: { cookie: "pf_access=tok-1" },
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
      makeJar({ pf_access: "old-tok", pf_refresh: "ref-tok" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "content-type": "application/json" },
      }));
    vi.mocked(refreshTokens).mockResolvedValue(REFRESHED_TOKENS);

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", { method: "GET" });
    const res = await GET(req, ctx(["projects"]));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retryHeaders = sentHeaders(fetchMock.mock.calls[1]);
    expect(retryHeaders.get("authorization")).toBe("Bearer new-access");

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });

    const setCookies = res.headers.getSetCookie();
    const joined = setCookies.join("\n");
    expect(joined).toContain("pf_access=new-access");
    expect(joined).toContain("pf_id=new-id");
    expect(joined).not.toContain("pf_refresh="); // refresh 토큰 자체는 새로 오지 않으므로 다시 쓰지 않는다
    for (const c of setCookies) {
      expect(c).toMatch(/Path=\//);
      expect(c).toMatch(/Max-Age=3600/);
      expect(c).toMatch(/HttpOnly/i);
      expect(c).toMatch(/SameSite=Lax/i);
    }
  });

  it("401 on a GET: refreshTokens throws (expired/no Cognito env) — original 401 passes through, no Set-Cookie", async () => {
    vi.mocked(cookies).mockResolvedValue(
      makeJar({ pf_access: "old-tok", pf_refresh: "ref-tok" }) as never);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }));
    vi.mocked(refreshTokens).mockRejectedValue(new Error("token endpoint rejected the request"));
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { GET } = await import("./route");
    const req = new NextRequest("https://app.example.com/api/projects", { method: "GET" });
    const res = await GET(req, ctx(["projects"]));

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1); // refresh itself failed — no retry fetch
    expect(res.headers.getSetCookie()).toHaveLength(0);
    errSpy.mockRestore();
  });

  it("401 on a POST: not retried — a streamed request body cannot be replayed", async () => {
    vi.mocked(cookies).mockResolvedValue(
      makeJar({ pf_access: "tok-1", pf_refresh: "ref-tok" }) as never);
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
      makeJar({ pf_access: "old-tok", pf_refresh: "ref-tok" }) as never);
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
