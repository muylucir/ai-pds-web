// frontend/lib/auth/gate.ts
//
// The middleware's decision logic. It is separated from middleware.ts because this list is the
// easiest part of this project to get wrong, so we want to assert it directly.
//
// ⚠️ This is a UX gate. It does not verify the cookie's signature, so a forged cookie can open the
// /admin screen, but every API that screen calls is blocked with a 403 by the backend.
// The security boundary is the backend's require_admin.
import { decodeJwtPayload, roleFromClaims } from "./claims";

export type GateDecision =
  | { kind: "allow" }
  | { kind: "login"; next: string }
  | { kind: "home" };

// Paths that must be accessible without login. Corresponds to the backend's
// tests/test_auth_route_coverage.py::PUBLIC_PATHS.
//   /login       the login screen itself
//   /survey/*    anonymous surveys (end users without accounts)
//   /proto/*     prototype previews (the same users actually try out the app)
//   /api/auth/*  the login round trip itself
//   /manual      the user manual — **the only entry with no corresponding backend path.**
//                It is a static document rendered only inside the frontend and calls no API,
//                so it has no counterpart in the backend's PUBLIC_PATHS. Putting it behind login
//                would leave no way to read "what this tool does" before receiving an account —
//                a link in the invitation email is how people first meet this tool.
const PUBLIC_PREFIXES = ["/login", "/survey/", "/proto/", "/api/auth/"];

// /api/* is let through: the proxy attaches the Bearer and the backend decides. Redirecting here
// would make fetch receive the HTML login page and break with a JSON parse error.
const PASSTHROUGH_PREFIXES = ["/api/"];

// Things public only at **exactly that path**, not as a prefix. They are single screens with no
// subpaths, so opening them with startsWith would let a future /manual/xxx inherit the decision —
// it is safer to keep the public list narrow than to widen it.
const PUBLIC_EXACT = ["/login", "/manual"];

function isPublic(pathname: string): boolean {
  if (PUBLIC_EXACT.includes(pathname)) return true;
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))
    || PASSTHROUGH_PREFIXES.some((p) => pathname.startsWith(p));
}

export function gateDecision(pathname: string,
                             accessToken: string | undefined): GateDecision {
  if (isPublic(pathname)) return { kind: "allow" };
  if (!accessToken) return { kind: "login", next: pathname };

  if (pathname.startsWith("/admin")) {
    // If the role cannot be confirmed (corrupted cookie, no group), do not open the admin screens.
    if (roleFromClaims(decodeJwtPayload(accessToken)) !== "admin") {
      return { kind: "home" };
    }
  }
  // If there is a cookie, let it through. Turning back an expired cookie here would bypass the
  // backend's refresh path and cause an endless round trip — the frontend handles 401s.
  return { kind: "allow" };
}
