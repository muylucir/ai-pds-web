// frontend/lib/auth/claims.ts
//
// ⚠️ The signature is NOT verified here. This module is for UX:
//   - when the middleware applies the /admin gate (role display/blocking)
//   - when /api/auth/me returns the email and role to show on screen
//
// The real line of defense is the backend's JWT verification. A user who forges a cookie can fool this file and
// open the /admin screen, but every API that screen calls is blocked with 403.
// Do not blur this distinction — mistaking the middleware for a security boundary is the
// classic failure of this pattern.

export interface Claims {
  [k: string]: unknown;
}

export function decodeJwtPayload(token: string): Claims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const json = Buffer.from(parts[1], "base64url").toString("utf8");
    const parsed = JSON.parse(json);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? parsed
      : null;
  } catch {
    // The middleware calls this — if it throws, every page becomes a 500.
    return null;
  }
}

// Must use the same precedence as the backend verifier (_role_from_groups): a user in both groups
// is admin. If they diverge, the screen and the actual permissions disagree.
export function roleFromClaims(c: Claims | null): "admin" | "pm" | null {
  const groups = c?.["cognito:groups"];
  if (!Array.isArray(groups)) return null;
  const names = groups.map(String);
  if (names.includes("admin")) return "admin";
  if (names.includes("pm")) return "pm";
  return null;
}

export function emailFromClaims(c: Claims | null): string | null {
  // The access token has no email, so this falls back to username.
  const email = c?.email ?? c?.username;
  return typeof email === "string" && email ? email : null;
}
