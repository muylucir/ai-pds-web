// frontend/lib/auth/cookies.ts
//
// The single source of cookie names and attributes. The 3 route handlers, the proxy, and the middleware
// must see the same names.

export const ACCESS_COOKIE = "aipds_access";
export const ID_COOKIE = "aipds_id";
export const REFRESH_COOKIE = "aipds_refresh";

// Values that exist only during the login round trip — consumed in the callback and deleted immediately.
export const VERIFIER_COOKIE = "aipds_pkce";
export const STATE_COOKIE = "aipds_state";
export const NEXT_COOKIE = "aipds_next";

const isProd = () => process.env.NODE_ENV === "production";

export function sessionCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,        // JS cannot read the token — cannot be stolen via XSS
    secure: isProd(),      // production only, so as not to block local http development
    // Why not strict: the cookie must ride along on the top-level redirect coming back from the Hosted UI.
    // With strict, the cookie is dropped from the request right after the callback and login loops forever.
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

export function transientCookieOptions() {
  // PKCE verifier / state / next: only need to live for the login round trip (up to 10 minutes).
  return { ...sessionCookieOptions(600) };
}

export function clearedCookieOptions() {
  return { ...sessionCookieOptions(0), maxAge: 0 };
}
