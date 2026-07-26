// frontend/app/api/auth/callback/route.ts
//
// 코드 교환의 유일한 장소. 서버사이드에서 일어나므로 토큰이 브라우저 JS에
// 도달하지 않는다 — httpOnly 쿠키에만 담긴다.
import { NextRequest, NextResponse } from "next/server";
import { cognitoEnv } from "@/lib/auth/cognitoUrls";
import { exchangeCode } from "@/lib/auth/tokenExchange";
import {
  ACCESS_COOKIE, ID_COOKIE, NEXT_COOKIE, REFRESH_COOKIE, STATE_COOKIE,
  VERIFIER_COOKIE, clearedCookieOptions, sessionCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const REFRESH_MAX_AGE = 30 * 24 * 60 * 60; // 풀 클라이언트의 refresh 유효기간과 일치

// NextResponse.redirect는 절대 URL만 받는다(상대 경로는 "URL is malformed"로
// 던진다 — 확인됨). req.url을 기준으로 조립하면 프록시 뒤에서도 현재 호스트를
// 그대로 쓴다.
function toLogin(req: NextRequest, reason: string): NextResponse {
  const url = new URL("/login", req.url);
  url.searchParams.set("error", reason);
  return NextResponse.redirect(url, 302);
}

function safeNext(raw: string | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export async function GET(request: NextRequest) {
  // 테스트는 NextRequest 대신 평범한 Request를 넘긴다(Next 런타임 밖에서는
  // nextUrl/cookies 게터가 없다) — 실제 요청은 이미 NextRequest이므로 no-op.
  const req = request instanceof NextRequest ? request : new NextRequest(request);
  const params = req.nextUrl.searchParams;

  // Hosted UI가 사용자 취소·설정 오류를 error로 알려준다.
  const hostedUiError = params.get("error");
  if (hostedUiError) return toLogin(req, hostedUiError);

  const code = params.get("code");
  const state = params.get("state");
  const expectedState = req.cookies.get(STATE_COOKIE)?.value;
  const verifier = req.cookies.get(VERIFIER_COOKIE)?.value;

  // CSRF 방어: 공격자가 유도한 콜백은 우리가 심은 state와 맞지 않는다.
  // verifier가 없으면(쿠키 만료·다른 브라우저) 교환 자체가 불가능하다.
  if (!code || !state || !expectedState || state !== expectedState || !verifier) {
    return toLogin(req, "state_mismatch");
  }

  let tokens;
  try {
    tokens = await exchangeCode(cognitoEnv(), code, verifier);
  } catch {
    // 사유는 서버 로그에만 — 사용자에게는 일반화된 오류를 보여준다.
    console.error("authorization code exchange failed");
    return toLogin(req, "exchange_failed");
  }

  const next = safeNext(req.cookies.get(NEXT_COOKIE)?.value);
  const res = NextResponse.redirect(new URL(next, req.url), 302);

  // access/id는 토큰 자체의 수명(expires_in), refresh는 30일.
  const session = sessionCookieOptions(tokens.expires_in);
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, session);
  res.cookies.set(ID_COOKIE, tokens.id_token, session);
  if (tokens.refresh_token) {
    res.cookies.set(REFRESH_COOKIE, tokens.refresh_token,
                    sessionCookieOptions(REFRESH_MAX_AGE));
  }
  // 왕복용 값은 소비했으므로 지운다 — 재사용(replay)을 막는다.
  for (const name of [VERIFIER_COOKIE, STATE_COOKIE, NEXT_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}
