// frontend/app/api/auth/logout/route.ts
//
// 쿠키를 지우고 Cognito 세션도 끊는다. 쿠키만 지우면 Hosted UI에 남은 세션
// 때문에 다음 로그인이 비밀번호를 묻지 않고 곧바로 통과한다(공용 PC 문제).
import { NextRequest, NextResponse } from "next/server";
import { cognitoEnv, logoutUrl } from "@/lib/auth/cognitoUrls";
import {
  ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, clearedCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function clearAll(res: NextResponse): NextResponse {
  for (const name of [ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}

export async function GET(req: NextRequest) {
  const env = cognitoEnv();
  if (!env.domain || !env.clientId) {
    return clearAll(NextResponse.redirect(new URL("/login", req.url)));
  }
  return clearAll(NextResponse.redirect(logoutUrl(env)));
}

// 헤더의 로그아웃 버튼이 POST로 부를 수 있게 한다(GET 로그아웃은 프리페치에
// 걸려 의도치 않게 세션을 끊을 수 있다).
export async function POST(req: NextRequest) {
  return GET(req);
}
