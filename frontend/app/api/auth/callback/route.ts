// frontend/app/api/auth/callback/route.ts
//
// The only place where the code exchange happens. It runs server-side, so the tokens never
// reach browser JS — they are held only in httpOnly cookies.
import { NextRequest, NextResponse } from "next/server";
import { cognitoEnv } from "@/lib/auth/cognitoUrls";
import { safeNext } from "@/lib/auth/safeNext";
import { redirectTo, redirectToLogin } from "@/lib/auth/redirectTo";
import { exchangeCode } from "@/lib/auth/tokenExchange";
import {
  ACCESS_COOKIE, ID_COOKIE, NEXT_COOKIE, REFRESH_COOKIE, STATE_COOKIE,
  VERIFIER_COOKIE, clearedCookieOptions, sessionCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const REFRESH_MAX_AGE = 30 * 24 * 60 * 60; // matches the pool client's refresh validity

// Location is a relative path — building an absolute URL from req.url leaks the internal
// address behind the proxy/CloudFront (an observed bug; see lib/auth/redirectTo.ts for why).
//
// The failure path also clears the round-trip cookies (aipds_pkce/aipds_state/aipds_next) — if
// only the success path cleared them, a failed attempt's PKCE material would stay in the
// browser, left alone until the next sign-in overwrote it.
function toLogin(reason: string): NextResponse {
  const res = redirectToLogin(reason, 302);
  for (const name of [VERIFIER_COOKIE, STATE_COOKIE, NEXT_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}

export async function GET(request: NextRequest) {
  // Tests pass a plain Request instead of a NextRequest (outside the Next runtime there are no
  // nextUrl/cookies getters) — a real request is already a NextRequest, so this is a no-op.
  const req = request instanceof NextRequest ? request : new NextRequest(request);
  const params = req.nextUrl.searchParams;

  // The Hosted UI reports user cancellation and configuration errors via error.
  const hostedUiError = params.get("error");
  if (hostedUiError) return toLogin(hostedUiError);

  const code = params.get("code");
  const state = params.get("state");
  const expectedState = req.cookies.get(STATE_COOKIE)?.value;
  const verifier = req.cookies.get(VERIFIER_COOKIE)?.value;

  // CSRF defense: a callback induced by an attacker will not match the state we planted.
  // Without the verifier (expired cookie, different browser) the exchange is impossible anyway.
  if (!code || !state || !expectedState || state !== expectedState || !verifier) {
    return toLogin("state_mismatch");
  }

  let tokens;
  try {
    tokens = await exchangeCode(cognitoEnv(), code, verifier);
  } catch (err) {
    // The detailed reason goes only to the server log — the user sees a generalized error.
    // TokenExchangeError's message records which of the three failure shapes (HTTP error,
    // non-JSON response, missing token) it was, so discarding it would leave nothing to
    // debug with.
    const reason = err instanceof Error ? err.message : String(err);
    console.error(`authorization code exchange failed: ${reason}`);
    return toLogin("exchange_failed");
  }

  const next = safeNext(req.cookies.get(NEXT_COOKIE)?.value, req.url);
  // safeNext already guarantees a same-origin relative path (open redirect defense).
  const res = redirectTo(next, 302);

  // access/id use the token's own lifetime (expires_in); refresh is 30 days.
  const session = sessionCookieOptions(tokens.expires_in);
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, session);
  res.cookies.set(ID_COOKIE, tokens.id_token, session);
  if (tokens.refresh_token) {
    res.cookies.set(REFRESH_COOKIE, tokens.refresh_token,
                    sessionCookieOptions(REFRESH_MAX_AGE));
  }
  // The round-trip values have been consumed, so clear them — this prevents replay.
  for (const name of [VERIFIER_COOKIE, STATE_COOKIE, NEXT_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}
