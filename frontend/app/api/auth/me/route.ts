// frontend/app/api/auth/me/route.ts
//
// User information to show on screen. The client cannot read the token (httpOnly), so
// this route is the only window.
//
// email is read from the id token — the Cognito access token has no email claim.
// role is read from cognito:groups in the access token.
//
// ⚠️ The signature is not verified here. These are display values; the backend decides actual permissions.
import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, ID_COOKIE } from "@/lib/auth/cookies";
import { decodeJwtPayload, emailFromClaims, roleFromClaims } from "@/lib/auth/claims";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const access = req.cookies.get(ACCESS_COOKIE)?.value;
  if (!access) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  const accessClaims = decodeJwtPayload(access);
  const idClaims = decodeJwtPayload(req.cookies.get(ID_COOKIE)?.value ?? "");
  return NextResponse.json({
    authenticated: true,
    email: emailFromClaims(idClaims) ?? emailFromClaims(accessClaims),
    role: roleFromClaims(accessClaims),
  });
}
