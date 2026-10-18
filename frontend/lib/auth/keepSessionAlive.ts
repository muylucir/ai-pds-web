// frontend/lib/auth/keepSessionAlive.ts
//
// Refreshes the access token periodically. The key point is not waiting for a
// request to fail with 401 — the reason is written in app/api/auth/refresh/route.ts.
//
// In short: the /api proxy's refresh reacts only to a backend 401, but a
// prototype build lives for tens of minutes on a single SSE connection and never
// gets another 401 on that connection. So there is not a single refresh
// opportunity while the stream runs, and the access token (60 minutes) expires
// mid-build. The Discovery chat, with its repeated short turns, gets a refresh
// opportunity every turn, so this defect never surfaced there.
//
// Its role differs from sessionRecovery.ts: that one is after-the-fact handling
// that checks for expiry **after the stream dies** and sends the user to login;
// this one is prevention, keeping the token from expiring in the first place.
// Both are needed — once the 30-day refresh window ends, prevention is
// impossible and only after-the-fact handling remains.

//: Refresh interval. Kept well below half of the access token lifetime (60
//: minutes, ACCESS_TOKEN_VALIDITY_MINUTES in infra/lib/auth-client-config.ts).
//: Setting it close to 60 minutes opens an expiry window as soon as a single
//: refresh is delayed or fails. At 15 minutes there are 3 chances within one
//: token lifetime, so the session survives even two consecutive failures.
export const REFRESH_INTERVAL_MS = 15 * 60_000;

/** Starts periodic refresh and returns a function that stops it.
 *
 *  It does not refresh right at startup — when this function is called (right
 *  after login or on entering a screen) the token is already fresh, and there is
 *  no reason to hit Cognito on every screen.
 */
export function keepSessionAlive(): () => void {
  const timer = setInterval(() => {
    void (async () => {
      let res: Response;
      try {
        res = await fetch("/api/auth/refresh", { method: "POST" });
      } catch {
        // The network dropped briefly — that is not the end of the session, so
        // keep trying. Stopping here would make a user whose Wi-Fi dropped once
        // silently lose their session mid-build.
        return;
      }
      // 401 = the refresh token has expired (30 days) or been revoked. There is no
      // way to refresh, so stop polling — leaving it running would repeat a call
      // that is certain to fail at every interval.
      //
      // We do not redirect to /login here. This function is a background timer the
      // user may not even be looking at, and the decision to abruptly kick the user
      // off the screen they are working on is made by
      // sessionRecovery.redirectIfSessionExpired when a real request fails (it
      // already has the discipline of telling 5xx apart from 401).
      if (res.status === 401) clearInterval(timer);
    })();
  }, REFRESH_INTERVAL_MS);

  return () => clearInterval(timer);
}
