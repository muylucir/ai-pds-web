// Same-origin API proxy to the backend (dev/demo).
//
// The browser is remote (behind the frontend proxy) and cannot reach the
// backend's localhost:8000 directly, so the client calls same-origin /api/*
// (NEXT_PUBLIC_API_BASE_URL=/api) and this handler forwards server-side.
//
// Why a route handler instead of next.config `rewrites()`: rewrites use a
// node-http-proxy that passes the backend's hop-by-hop `Connection: keep-alive`
// header through (and appends `close`), which is illegal over HTTP/2 and breaks
// SSE in the browser (ERR_HTTP2_PROTOCOL_ERROR). Here we re-stream the backend
// body through a fresh Response, copying ONLY safe headers — no hop-by-hop
// headers reach the HTTP/2 downstream. The streamed body (Response(res.body))
// preserves SSE chunk-by-chunk delivery.
//
// This is a dev/demo convenience; production should sit behind a real reverse
// proxy (and carry auth) instead of routing API traffic through Next.
import { NextRequest } from "next/server";
import { rewriteLocation } from "@/lib/api/rewriteLocation";
import { cookies } from "next/headers";
import { ACCESS_COOKIE, REFRESH_COOKIE, sessionCookieOptions } from "@/lib/auth/cookies";
import { ID_COOKIE } from "@/lib/auth/cookies";
import { isRetryableWithRefresh, withBearer } from "@/lib/api/proxyAuth";
import { cognitoEnv } from "@/lib/auth/cognitoUrls";
import { refreshTokens } from "@/lib/auth/tokenExchange";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const BACKEND = process.env.PATHFINDER_BACKEND_URL ?? "http://localhost:8000";

// Hop-by-hop headers must never cross a proxy boundary (RFC 7230 §6.1) and
// several are outright illegal over HTTP/2. Strip them from both directions.
const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade", "content-length",
  "content-encoding", "host",
  // 세션 쿠키는 이 경계에서 멈춘다: withBearer()가 Authorization으로 번역하고
  // 백엔드는 쿠키를 모른다.
  "cookie",
]);

function filterHeaders(src: Headers): Headers {
  const out = new Headers();
  src.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) out.append(key, value);
  });
  return out;
}

async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  const search = req.nextUrl.search;
  // Next's catch-all `path[]` has no empty final segment, so a request for
  // ".../demo/" would be forwarded as ".../demo" — the backend then redirects
  // back to the slash form and the browser loops. Carry the trailing slash
  // over from the incoming URL. (It is load-bearing: proxied prototypes use
  // relative asset refs that resolve against the directory form.)
  const trailingSlash = req.nextUrl.pathname.endsWith("/") ? "/" : "";
  const url = `${BACKEND}/${path.map(encodeURIComponent).join("/")}${trailingSlash}${search}`;

  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;

  const send = async (token: string | undefined): Promise<Response> => {
    const init: RequestInit & { duplex?: "half" } = {
      method: req.method,
      // 쿠키를 Bearer로 번역한다. EventSource는 커스텀 헤더를 못 보내지만
      // same-origin 쿠키는 자동으로 보내므로, SSE가 이 경로를 타면 인증된다.
      headers: withBearer(filterHeaders(req.headers), token),
      redirect: "manual",
    };
    if (req.method !== "GET" && req.method !== "HEAD") {
      init.body = req.body;
      init.duplex = "half"; // required by undici when streaming a request body
    }
    return fetch(url, init);
  };

  let res = await send(access);
  let refreshedCookies: { access: string; id: string; expiresIn: number } | null = null;

  // access 토큰 만료: 리프레시 후 한 번 재시도한다. 본문이 스트림인 메서드는
  // 재생할 수 없으므로 제외한다(isRetryableWithRefresh) — 그런 요청은 401이
  // 그대로 흘러 프론트가 /login으로 보낸다.
  if (isRetryableWithRefresh(res.status, req.method, Boolean(refresh))) {
    try {
      const tokens = await refreshTokens(cognitoEnv(), refresh as string);
      refreshedCookies = {
        access: tokens.access_token, id: tokens.id_token,
        expiresIn: tokens.expires_in,
      };
      res = await send(tokens.access_token);
    } catch {
      // 리프레시 토큰이 만료·폐기됐다 — 원래의 401을 그대로 흘린다.
      console.error("token refresh failed; passing 401 through");
    }
  }

  // Re-stream the (possibly SSE) body with clean headers only. Response(body)
  // uses the platform's own framing, so no forbidden HTTP/2 headers leak.
  const headers = filterHeaders(res.headers);
  // The backend names its own origin in redirects (e.g. Starlette's absolute
  // 307 for a missing trailing slash). Passed through verbatim, that walks the
  // browser off the public host onto localhost:8000, which it cannot reach —
  // it just hangs. Re-anchor any self-referential redirect under /api.
  const location = headers.get("location");
  if (location) headers.set("location", rewriteLocation(location, BACKEND));

  const out = new Response(res.body, { status: res.status, headers });
  // 갱신된 토큰을 브라우저 쿠키에 반영한다 — 하지 않으면 매 요청이 만료된
  // 토큰으로 시작해 리프레시를 반복한다.
  if (refreshedCookies) {
    const opts = sessionCookieOptions(refreshedCookies.expiresIn);
    const attrs = `Path=${opts.path}; Max-Age=${opts.maxAge}; HttpOnly; SameSite=Lax`
      + (opts.secure ? "; Secure" : "");
    out.headers.append("set-cookie",
      `${ACCESS_COOKIE}=${refreshedCookies.access}; ${attrs}`);
    out.headers.append("set-cookie",
      `${ID_COOKIE}=${refreshedCookies.id}; ${attrs}`);
  }
  return out;
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
