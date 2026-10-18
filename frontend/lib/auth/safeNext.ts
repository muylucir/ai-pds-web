// frontend/lib/auth/safeNext.ts
//
// The single validation point for the path to return to after login. Used by both login and
// callback — validation copy-pasted into two places becomes validation fixed in only one.
//
// Postcondition — not something callers must uphold, but something this function guarantees:
//   the return value is always safe to put back into `new URL(returnValue, requestUrl)`, and the
//   result always resolves to our own origin. If this guarantee breaks, any caller that
//   resolves the return value again (none today, but one could appear later) is exposed to an
//   open redirect.
//
// ⚠️ A string prefix check (looking only at whether it starts with "//") is completely broken
// through: "/\evil.example" (one slash + a backslash) does not start with "//", but the WHATWG
// URL parser treats a backslash like a slash in special schemes and resolves it to
// https://evil.example/. So here "does raw start with /" is used only to filter early, and the
// final decision is always made by resolving with the real URL parser and then comparing origins
// — even if a new encoding trick appears, the origin comparison is not bypassed.
export function safeNext(raw: string | null | undefined,
                         requestUrl: string): string {
  if (!raw) return "/";
  // A backslash never appears in the paths we generate. It is rejected outright, including the
  // cases that resolve to same-origin — a predictable decision that does not rely on the parser's
  // backslash handling.
  if (raw.includes("\\")) return "/";

  let candidate: URL;
  let origin: URL;
  try {
    origin = new URL(requestUrl);
    candidate = new URL(raw, origin);
  } catch {
    return "/";
  }
  // The real line of defense: not the shape of the string, but whether the resolved origin is
  // our own.
  if (candidate.origin !== origin.origin) return "/";

  const result = `${candidate.pathname}${candidate.search}${candidate.hash}`;
  // Even after passing the same-origin check, if raw spelled out our own origin as an
  // absolute URL (e.g., "https://app.example.com//evil.example"), the pathname itself can
  // start with "//". If this string were later put back into `new URL(result, someUrl)`, it
  // would be reinterpreted as protocol-relative and bounce offsite — neither current caller
  // reuses it this way, so it does not fire, but to uphold the postcondition unconditionally
  // it must be filtered here too.
  if (result.startsWith("//")) return "/";
  return result;
}
