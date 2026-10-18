// frontend/lib/api/proxyAuth.ts
//
// The auth part of the /api proxy. Split out because a route file cannot export helpers
// (same reason as rewriteLocation.ts).

// Methods whose request body cannot be replayed — retrying after a 401 refresh is impossible.
// (If init.body is a stream, it cannot be rewound once consumed.)
const REPLAYABLE_METHODS = new Set(["GET", "HEAD", "DELETE"]);

//: Prefix of cookies that may be passed through to the backend. These are the prototype access token cookies
//: (COOKIE_PREFIX in backend/aipds/routes/proto_public.py) — both sides must be the same
//: string; if they disagree, the cookie never reaches the backend and every prototype
//: preview returns 404.
const PROTO_COOKIE_PREFIX = "aipds_proto_";

/** Cookie header value keeping only the cookies that may be sent to the backend. null if none.
 *
 *  **It is an allowlist** (not a blocklist). Why this direction is load-bearing: if another
 *  session-related cookie appears later, with a blocklist the moment someone forgets to add it
 *  here it silently leaks to the backend, and that breaks no test.
 *  With an allowlist the same mistake shows up as "the new cookie is not forwarded", failing
 *  toward the feature not working.
 *
 *  Only the prototype access cookie is let through because it is **the only cookie the backend
 *  has to judge**. The session JWTs (aipds_access/aipds_id/aipds_refresh) stop at this
 *  boundary and withBearer translates them into Authorization — the backend does not read session
 *  cookies. */
export function forwardableCookies(cookieHeader: string | null | undefined): string | null {
  if (!cookieHeader) return null;
  const kept = cookieHeader
    .split(";")
    .map((c) => c.trim())
    // Judged by name. It is a prefix check, so there is no need to split off the value — cookie names
    // cannot contain "=", so `startsWith` is guaranteed to look only at the start of the name.
    .filter((c) => c.startsWith(PROTO_COOKIE_PREFIX));
  return kept.length > 0 ? kept.join("; ") : null;
}

export function withBearer(headers: Headers,
                           accessToken: string | undefined): Headers {
  const out = new Headers(headers);
  // The backend does not know about session cookies. Passing them through would expose the session tokens one layer further.
  // Delete them all, then restore only the allowlist — deleting must be the default.
  const forwardable = forwardableCookies(out.get("cookie"));
  out.delete("cookie");
  // The prototype access cookie is the only cookie the backend judges
  // (_authorized in routes/proto_public.py). Without it, every /api/proto/* request
  // returns 404 — because that path also goes through this proxy.
  if (forwardable) out.set("cookie", forwardable);
  // The Authorization the client sent is not trusted — the httpOnly cookie is the truth.
  out.delete("authorization");
  if (accessToken) out.set("authorization", `Bearer ${accessToken}`);
  return out;
}

export function isRetryableWithRefresh(status: number, method: string,
                                       hasRefresh: boolean): boolean {
  return status === 401 && hasRefresh
    && REPLAYABLE_METHODS.has(method.toUpperCase());
}
