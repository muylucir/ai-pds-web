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

//: The namespace of every cookie this app sets (lib/auth/cookies.ts: the session JWTs, the PKCE
//: verifier, the login state and return path). A preview request drops this namespace, not a list of
//: names — a cookie added to the app later is covered by its name alone.
const APP_COOKIE_PREFIX = "aipds_";

//: The first path segment of a prototype preview request (`/api/proto/...` in the browser). The
//: backend's preview proxy forwards the Cookie header to the prototype as is (proto_public.py).
export const PREVIEW_SEGMENT = "proto";

function cookieName(pair: string): string {
  return pair.slice(0, pair.indexOf("=") === -1 ? pair.length : pair.indexOf("="));
}

/** Cookie header value keeping only the cookies that may be sent to the backend. null if none.
 *
 *  **API requests: an allowlist** (not a blocklist). Why this direction is load-bearing: if another
 *  session-related cookie appears later, with a blocklist the moment someone forgets to add it
 *  here it silently leaks to the backend, and that breaks no test.
 *  With an allowlist the same mistake shows up as "the new cookie is not forwarded", failing
 *  toward the feature not working.
 *  Only the prototype access cookie is let through because it is **the only cookie the backend
 *  has to judge**. The session JWTs (aipds_access/aipds_id/aipds_refresh) stop at this
 *  boundary and withBearer translates them into Authorization — the backend does not read session
 *  cookies.
 *
 *  **Preview requests (`preview`): everything outside the app's namespace.** A prototype keeps its
 *  own login in its own cookies — a persona demo signs in as one role and moves between screens —
 *  and the allowlist above dropped every one of them: the prototype saw each request after login
 *  as signed out and sent the user back to persona selection (workshop report, 2026-10-27).
 *  An allowlist cannot work here, because the names are whatever the generated code chose. What
 *  must never reach a prototype is this app's own cookies, and those share one prefix, so that is
 *  what is dropped — the same failure direction as above: an app cookie named outside the
 *  namespace would be a bug in lib/auth/cookies.ts, not here. On a deployment with a preview
 *  surface the app cookies do not even arrive (a different site, aipds/preview_surface.py); this
 *  still holds where the app origin serves previews (local development).
 */
export function forwardableCookies(cookieHeader: string | null | undefined,
                                   { preview = false }: { preview?: boolean } = {}): string | null {
  if (!cookieHeader) return null;
  const kept = cookieHeader
    .split(";")
    .map((c) => c.trim())
    .filter((c) => c !== "")
    // Judged by name. It is a prefix check, so there is no need to split off the value — cookie names
    // cannot contain "=", so `startsWith` is guaranteed to look only at the start of the name.
    .filter((c) => c.startsWith(PROTO_COOKIE_PREFIX)
      || (preview && !cookieName(c).startsWith(APP_COOKIE_PREFIX)));
  return kept.length > 0 ? kept.join("; ") : null;
}

export function withBearer(headers: Headers,
                           accessToken: string | undefined,
                           { preview = false }: { preview?: boolean } = {}): Headers {
  const out = new Headers(headers);
  // The backend does not know about session cookies. Passing them through would expose the session tokens one layer further.
  // Delete them all, then restore only what may pass — deleting must be the default.
  const forwardable = forwardableCookies(out.get("cookie"), { preview });
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
