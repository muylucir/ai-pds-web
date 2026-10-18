// frontend/app/api/auth/logout/route.ts
//
// Clears the cookies and also ends the Cognito session. Clearing only the cookies would let the next login
// pass straight through without asking for a password because of the session left in the Hosted UI (the shared-PC problem).
import { NextRequest, NextResponse } from "next/server";
import { cognitoEnv, logoutUrl } from "@/lib/auth/cognitoUrls";
import { redirectToLogin } from "@/lib/auth/redirectTo";
import {
  ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, clearedCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// **303 See Other.** This number is the most important thing in this file.
//
// The logout button arrives as a POST (see the POST handler comment below). By spec, 303 is the only code
// that makes the browser follow a redirect **as GET**. 307/308
// preserve the method, and the default of Next's `NextResponse.redirect()` is exactly 307
// (next/dist/server/web/spec-extension/response.js: `?? 307`).
//
// That default was an observed defect (2026-08-04): the button's POST was reissued as a POST to Cognito Hosted UI's
// `/logout`, and that endpoint accepts only GET, so it returned 405
// (Method Not Allowed) — logout did not happen.
//
// This failure **leaves nothing in our logs.** Our response was a normal 307,
// and the 405 was issued by Cognito directly to the browser, so the evidence was only in the browser console.
// That is why the number is stated explicitly rather than left to the default.
//
// 302 also turns into GET in practice, but that is historical convention, not guaranteed by the spec.
// 303's very definition is "change the method to GET and follow".
const SEE_OTHER = 303;

function clearAll(res: NextResponse): NextResponse {
  for (const name of [ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}

// req is not used (the redirect became a relative path, so req.url is no longer needed).
// The signature is Next's route handler contract, so it stays with only its name marked with an underscore.
export async function GET(_req: NextRequest) {
  const env = cognitoEnv();
  if (!env.domain || !env.clientId) {
    // Relative Location (lib/auth/redirectTo.ts). The Hosted UI logoutUrl must be an external
    // absolute URL, so the one below stays as is.
    //
    // This path is also 303, not 302 — a logout that came in as POST can land here,
    // and `/login` is a page (GET). Using redirectToLogin's default (307) as is
    // would reproduce the same defect in local/bypass environments.
    return clearAll(redirectToLogin(undefined, SEE_OTHER));
  }
  return clearAll(NextResponse.redirect(logoutUrl(env), SEE_OTHER));
}

// Lets the header's logout button call it with POST (a GET logout can be caught by prefetching
// and end the session unintentionally).
export async function POST(req: NextRequest) {
  return GET(req);
}
