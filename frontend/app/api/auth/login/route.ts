// frontend/app/api/auth/login/route.ts
//
// Login start: creates the PKCE verifier and state, plants them in httpOnly cookies, and sends the user to
// the Hosted UI. Because the verifier exists only in a server cookie, browser JS never holds the values
// needed for the code exchange.
import { NextRequest, NextResponse } from "next/server";
import { authorizeUrl, cognitoEnv } from "@/lib/auth/cognitoUrls";
import { challengeFor, randomUrlSafe } from "@/lib/auth/pkce";
import { safeNext } from "@/lib/auth/safeNext";
import { redirectToLogin } from "@/lib/auth/redirectTo";
import {
  NEXT_COOKIE, STATE_COOKIE, VERIFIER_COOKIE, transientCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const env = cognitoEnv();
  if (!env.domain || !env.clientId) {
    // A deployment without auth configured — the login screen shows an explanatory message.
    // Relative Location — behind a proxy req.url leaks the internal address
    // (lib/auth/redirectTo.ts). The authorizeUrl going out to the Hosted UI must be an external
    // absolute URL, so it is left as is.
    return redirectToLogin("not_configured");
  }
  const verifier = randomUrlSafe();
  const state = randomUrlSafe(16);
  const res = NextResponse.redirect(
    authorizeUrl(env, await challengeFor(verifier), state));
  res.cookies.set(VERIFIER_COOKIE, verifier, transientCookieOptions());
  res.cookies.set(STATE_COOKIE, state, transientCookieOptions());
  res.cookies.set(NEXT_COOKIE,
                  safeNext(req.nextUrl.searchParams.get("next"), req.url),
                  transientCookieOptions());
  return res;
}
