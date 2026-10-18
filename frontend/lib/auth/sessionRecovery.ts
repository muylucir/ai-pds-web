// frontend/lib/auth/sessionRecovery.ts
//
// EventSource does not expose the response status code — whether it is a 401 (token expired) or a network
// drop, both arrive only as onerror. After the stream dies, check the session once and,
// if it has expired, send the user to login. Otherwise the user keeps seeing "연결이 끊어졌습니다" (connection lost)
// over and over with no idea why it is not working.
//
// The same is why the /api proxy's refresh does not apply to streams: SSE is already
// streaming the response, so retrying at the point a 401 arrives is meaningless.

// Why navigate is injected: hooks pass next/navigation's router.push, and
// tests pass a spy. The default is a full page navigation —
// the login round trip discards app state anyway, so client-side routing has no benefit.
function defaultNavigate(url: string): void {
  window.location.assign(url);
}

// Concurrency note: if several SSE hooks drop at the same moment, each may call this function
// independently and each may call navigate — there is no in-flight guard.
// It is harmless as long as it converges on window.location.assign(same URL): unlike history.pushState
// nothing piles up; it is just duplicate page navigations to the same destination.
export async function redirectIfSessionExpired(
  navigate: (url: string) => void = defaultNavigate,
  currentPath?: string,
): Promise<boolean> {
  let res: Response;
  try {
    res = await fetch("/api/auth/me", { credentials: "include" });
  } catch {
    // The check itself failed — the backend may just be down for a moment, so do not kick the user
    // off the screen they are working on.
    return false;
  }
  if (res.ok) return false; // 200 — the session is alive.

  // Only 401 is treated as a reliable signal of "no session" — the only failure status this endpoint
  // (app/api/auth/me/route.ts) actually produces is 401. A 5xx may just mean
  // the backend is briefly down, unrelated to the session, so it is not taken as expiry
  // — otherwise a backend that is deploying or restarting would kick users with valid sessions
  // off the screen they are working on.
  if (res.status !== 401) return false;

  // Rather than concluding from the status code alone, it double-checks with authenticated:false
  // in the body. If the body is broken (parse failure), that is "undecidable", not "confirmed
  // expired" — misreading a parse failure as authenticated:false would
  // navigate away even in undecidable situations.
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return false;
  }
  const expired =
    typeof body === "object" && body !== null &&
    (body as { authenticated?: unknown }).authenticated === false;
  if (!expired) return false;

  const next = currentPath
    ? `/login?next=${encodeURIComponent(currentPath)}`
    : "/login";
  navigate(next);
  return true;
}
