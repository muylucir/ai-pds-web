// frontend/lib/auth/redirectTo.ts
//
// The single point that creates redirects into the app.
//
// Why not use NextResponse.redirect(new URL(path, req.url)):
// Next 15 assembles req.url in middleware/route handlers from the server's own known origin,
// not the Host header. So behind a proxy (nginx) + CloudFront, the internal address leaks out
// as Location — measured deployment bug: connecting via CloudFront redirected to
// `https://localhost:3000/login?next=%2F`. Whether EC2 was given the CloudFront domain as Host
// or given X-Forwarded-Host, the result was the same (nginx was innocent; next start's
// `-H 127.0.0.1` binding was the source of the origin).
//
// A relative Location is resolved by the browser against the current origin, so it is right in
// CloudFront, reverse proxies, and local dev alike. Since RFC 7231 the requirement for an absolute
// URL in Location is gone, so relative values are standard. Confirmed in the deployment environment:
// `location: /login?error=access_denied` goes out as is and the browser resolves it against the
// CloudFront origin.
//
// ⚠️ **It cannot be used in middleware.** Middleware runs on the Edge runtime, and that runtime
// internally parses the response's location header with new URL(), so a relative value causes a
// 500 with `TypeError: Invalid URL` (measured). Middleware assembles an absolute URL from the Host
// header — see selfUrl in middleware.ts. Every route handler that uses this helper is
// `runtime = "nodejs"` and does not go through that parsing.
//
// Redirects going outside (Cognito Hosted UI) must be absolute URLs, so they do not use this
// helper — those are assembled by cognitoUrls.ts with APP_BASE_URL.
import { NextResponse } from "next/server";

/** Allowed status codes.
 *
 * Why 303 is included: when sending a request that came in as POST (the logout form) to a GET page,
 * it **must** be 303. 307/308 preserve the method, so the POST is reissued as is —
 * that was a measured defect (the SEE_OTHER comment in app/api/auth/logout/route.ts).
 */
export type RedirectStatus = 302 | 303 | 307;

/**
 * Redirect to an in-app path. `path` must be a relative path starting with "/".
 *
 * NextResponse.redirect() forces URL parsing and rejects relative values (“URL is
 * malformed”), so we build the response ourselves.
 */
export function redirectTo(path: string, status: RedirectStatus = 307): NextResponse {
  // Open redirect defense: if a protocol-relative ("//evil.example") or absolute URL slips in,
  // it pretends to be relative and bounces off-site. The principle is that callers pass values
  // that went through safeNext, but the invariant is kept at this point too.
  const safe = path.startsWith("/") && !path.startsWith("//") ? path : "/";
  return new NextResponse(null, { status, headers: { location: safe } });
}

/** Send to `/login`, carrying the reason in the query (the login screen translates it into Korean). */
export function redirectToLogin(reason?: string, status: RedirectStatus = 307): NextResponse {
  const path = reason ? `/login?error=${encodeURIComponent(reason)}` : "/login";
  return redirectTo(path, status);
}
