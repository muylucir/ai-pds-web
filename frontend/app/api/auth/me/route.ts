// frontend/app/api/auth/me/route.ts
//
// 화면에 보여줄 사용자 정보. 클라이언트가 토큰을 읽을 수 없으므로(httpOnly)
// 이 경로가 유일한 창구다.
//
// email은 id 토큰에서 읽는다 — Cognito access 토큰에는 email 클레임이 없다.
// role은 access 토큰의 cognito:groups에서 읽는다.
//
// ⚠️ 여기서 서명을 검증하지 않는다. 표시용 값이며, 실제 권한은 백엔드가 판단한다.
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
