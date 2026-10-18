// frontend/middleware.ts
//
// Sends browsers that are not logged in to /login, and turns back a pm who types an admin
// screen URL directly. The judgment is made by lib/auth/gate.ts; this file handles only
// request/response.
//
// ⚠️ This is not a security boundary — it does not verify the cookie signature. The real
// authorization is the backend's require_user / require_admin, and even if a forged cookie opens
// the screen, the API blocks everything.
import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE } from "@/lib/auth/cookies";
import { gateDecision } from "@/lib/auth/gate";

// The redirect target origin comes from the **Host header**. Not req.url.
//
// Why not req.url: Next 15 assembles req.url from the server's own known origin, not the Host
// header. next start is bound with `-H 127.0.0.1`, so `new URL(path, req.url)` always produces
// the internal address behind a proxy/CloudFront — a measured deployment bug: accessing via
// CloudFront gave a Location of
// `https://localhost:3000/login?next=%2F`.
//
// Why not a relative path: middleware runs in the Edge runtime, and that runtime parses the
// response's location header internally with new URL(). Giving it a relative value makes every
// page a 500 with `TypeError: Invalid URL, input: '/login?next=%2F'` (measured). A route handler
// (Node runtime) accepts relative values, but here it does not.
//
// Host is a value the user can manipulate, but it is safe here: the URL built from it is only a
// same-path redirect (we decide the path), and it is not used for authorization. Behind the
// proxy, nginx passes the actual host that was accessed through as is
// (proxy_set_header Host $host).
function selfUrl(req: NextRequest, path: string): string {
  const host = req.headers.get("host");
  // If x-forwarded-proto is present, it is the scheme the user used (nginx sets https). If not,
  // the request scheme — local http dev is not forced to https.
  const proto = req.headers.get("x-forwarded-proto")?.split(",")[0].trim()
    || req.nextUrl.protocol.replace(":", "");
  if (host) {
    try {
      // If Host is manipulated or corrupted (parse failure), it falls through to the fallback
      // below. If the middleware throws, every page becomes a 500, so it must never throw.
      return new URL(path, `${proto}://${host}`).toString();
    } catch {
      // fall through
    }
  }
  return new URL(path, req.url).toString();
}

export function middleware(req: NextRequest) {
  const decision = gateDecision(req.nextUrl.pathname,
                                req.cookies.get(ACCESS_COOKIE)?.value);
  if (decision.kind === "allow") return NextResponse.next();
  if (decision.kind === "home") {
    return NextResponse.redirect(selfUrl(req, "/"));
  }
  return NextResponse.redirect(
    selfUrl(req, `/login?next=${encodeURIComponent(decision.next)}`));
}

export const config = {
  // Static assets and the favicon are not subject to the judgment (running the middleware on
  // every request for them is waste). Public path judgment is done by gate.ts, so they are
  // not excluded here — if the list were scattered across two places, they would drift apart.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
