// Redirect-target rewriting for the same-origin /api proxy.
//
// Lives outside app/api/[...path]/route.ts because Next allows only route
// handlers (GET/POST/...) and a fixed set of config exports from a route file —
// any other export fails the build's generated type check.
export const DEFAULT_BACKEND =
  process.env.AIPDS_BACKEND_URL ?? "http://localhost:8000";

// Map a backend redirect target onto the same-origin proxy. Absolute URLs that
// point at the backend become "/api/<path>"; bare absolute paths get the same
// prefix. A redirect to a genuinely different host is left untouched.
//
// Why this matters: the backend names its OWN origin in redirects (e.g.
// Starlette's absolute 307 when a trailing slash is missing). Passed through
// verbatim, that walks the browser off the public host onto localhost:8000,
// which it cannot reach — the page just hangs.
export function rewriteLocation(
  value: string,
  backend: string = DEFAULT_BACKEND,
): string {
  let path: string;
  let rest = "";
  if (/^https?:\/\//i.test(value)) {
    if (!value.startsWith(backend)) return value;
    const u = new URL(value);
    path = u.pathname;
    rest = u.search + u.hash;
  } else if (value.startsWith("/")) {
    const idx = value.search(/[?#]/);
    path = idx === -1 ? value : value.slice(0, idx);
    rest = idx === -1 ? "" : value.slice(idx);
  } else {
    return value; // relative — resolved by the browser against the current URL
  }
  if (path.startsWith("/api/")) return `${path}${rest}`; // already anchored
  return `/api${path}${rest}`;
}
