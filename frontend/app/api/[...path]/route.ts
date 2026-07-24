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

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const BACKEND = process.env.PATHFINDER_BACKEND_URL ?? "http://localhost:8000";

// Hop-by-hop headers must never cross a proxy boundary (RFC 7230 §6.1) and
// several are outright illegal over HTTP/2. Strip them from both directions.
const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade", "content-length",
  "content-encoding", "host",
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
  const url = `${BACKEND}/${path.map(encodeURIComponent).join("/")}${search}`;

  const init: RequestInit & { duplex?: "half" } = {
    method: req.method,
    headers: filterHeaders(req.headers),
    redirect: "manual",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = req.body;
    init.duplex = "half"; // required by undici when streaming a request body
  }

  const res = await fetch(url, init);
  // Re-stream the (possibly SSE) body with clean headers only. Response(body)
  // uses the platform's own framing, so no forbidden HTTP/2 headers leak.
  const headers = filterHeaders(res.headers);
  // The backend names its own origin in redirects (e.g. Starlette's absolute
  // 307 for a missing trailing slash). Passed through verbatim, that walks the
  // browser off the public host onto localhost:8000, which it cannot reach —
  // it just hangs. Re-anchor any self-referential redirect under /api.
  const location = headers.get("location");
  if (location) headers.set("location", rewriteLocation(location, BACKEND));
  return new Response(res.body, {
    status: res.status,
    headers,
  });
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
