// frontend/app/api/auth/login/route.ts
//
// 로그인 시작: PKCE verifier와 state를 만들어 httpOnly 쿠키에 심고 Hosted UI로
// 보낸다. verifier가 서버 쿠키에만 존재하므로 브라우저 JS는 코드 교환에
// 필요한 값을 갖지 못한다.
import { NextRequest, NextResponse } from "next/server";
import { authorizeUrl, cognitoEnv } from "@/lib/auth/cognitoUrls";
import { challengeFor, randomUrlSafe } from "@/lib/auth/pkce";
import {
  NEXT_COOKIE, STATE_COOKIE, VERIFIER_COOKIE, transientCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// 로그인 후 돌아갈 경로. 우리 사이트 내부만 허용한다(open redirect 방어).
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export async function GET(req: NextRequest) {
  const env = cognitoEnv();
  if (!env.domain || !env.clientId) {
    // 인증이 설정되지 않은 배포 — 로그인 화면이 안내 문구를 보여준다.
    return NextResponse.redirect(new URL("/login?error=not_configured", req.url));
  }
  const verifier = randomUrlSafe();
  const state = randomUrlSafe(16);
  const res = NextResponse.redirect(
    authorizeUrl(env, await challengeFor(verifier), state));
  res.cookies.set(VERIFIER_COOKIE, verifier, transientCookieOptions());
  res.cookies.set(STATE_COOKIE, state, transientCookieOptions());
  res.cookies.set(NEXT_COOKIE, safeNext(req.nextUrl.searchParams.get("next")),
                  transientCookieOptions());
  return res;
}
