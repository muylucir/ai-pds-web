// frontend/app/api/auth/refresh/route.ts
//
// Refreshes the access/id tokens **proactively**. Not waiting for a request to fail with 401 is
// the reason this route exists.
//
// **Why it is needed (logouts during prototype builds).** The /api proxy's refresh fires only when
// the backend returns 401 (isRetryableWithRefresh in app/api/[...path]/route.ts).
// A long SSE stream never meets that condition: once `GET /events` opens with 200, no 401 will
// come back on that connection, so during the tens of minutes the stream runs there is not a
// single chance to refresh. The access token lasts 60 minutes and a prototype build comes close
// to that, so the session is found expired on the first request after the build ends.
//
// In Discovery chat, where short turns repeat, every turn is a refresh opportunity, so this defect
// did not surface — which is why it showed up only in prototypes.
//
// `/api/auth/me` cannot substitute: that route only checks for the cookie's **presence** and does not
// refresh, and it does not go through the /api proxy either, so it has nothing to do with the proxy's refresh path.
//
// The periodic client-side call is handled by lib/auth/keepSessionAlive.ts.
import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, sessionCookieOptions } from "@/lib/auth/cookies";
import { cognitoEnv } from "@/lib/auth/cognitoUrls";
import { refreshTokens } from "@/lib/auth/tokenExchange";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  // Tests pass a plain Request instead of a NextRequest (outside the Next runtime there is no
  // cookies getter) — real requests are already NextRequests, so this is a no-op.
  // callback/route.ts uses the same convention.
  const req = request instanceof NextRequest ? request : new NextRequest(request);
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  if (!refresh) {
    // Without a refresh token there is nothing to refresh. Cognito is not hit —
    // the call is certain to fail, and since this is a polling path that waste would repeat.
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }

  let tokens;
  try {
    tokens = await refreshTokens(cognitoEnv(), refresh);
  } catch (err) {
    // The refresh token has expired (30 days) or been revoked. Returning 200 here would make the client
    // misjudge the session as alive and keep polling, and the user would discover the logout only on
    // the next real request — the very failure shape this route was meant to prevent.
    const reason = err instanceof Error ? err.message : String(err);
    console.error(`proactive token refresh failed: ${reason}`);
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }

  const res = NextResponse.json({ authenticated: true });
  const session = sessionCookieOptions(tokens.expires_in);
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, session);
  res.cookies.set(ID_COOKIE, tokens.id_token, session);
  // REFRESH_COOKIE is left untouched. This pool has refresh rotation off
  // (infra/lib/auth-client-config.ts — refreshTokenRotationGracePeriod unset),
  // so the refresh grant does not return a new refresh_token, and overwriting it with a value
  // absent from the response would wipe out the entire 30-day window.
  return res;
}
