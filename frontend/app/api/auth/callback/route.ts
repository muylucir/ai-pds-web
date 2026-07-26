// frontend/app/api/auth/callback/route.ts
//
// 코드 교환의 유일한 장소. 서버사이드에서 일어나므로 토큰이 브라우저 JS에
// 도달하지 않는다 — httpOnly 쿠키에만 담긴다.
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

const REFRESH_MAX_AGE = 30 * 24 * 60 * 60; // 풀 클라이언트의 refresh 유효기간과 일치

// Location은 상대 경로다 — req.url로 절대 URL을 조립하면 프록시/CloudFront
// 뒤에서 내부 주소가 샌다(실측 버그, 이유는 lib/auth/redirectTo.ts 참조).
//
// 실패 경로도 왕복용 쿠키(pf_pkce/pf_state/pf_next)를 지운다 — 성공 경로에서만
// 지우면 실패한 시도의 PKCE 자재가 브라우저에 남아 다음 로그인이 덮어쓸 때까지
// 방치된다.
function toLogin(reason: string): NextResponse {
  const res = redirectToLogin(reason, 302);
  for (const name of [VERIFIER_COOKIE, STATE_COOKIE, NEXT_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}

export async function GET(request: NextRequest) {
  // 테스트는 NextRequest 대신 평범한 Request를 넘긴다(Next 런타임 밖에서는
  // nextUrl/cookies 게터가 없다) — 실제 요청은 이미 NextRequest이므로 no-op.
  const req = request instanceof NextRequest ? request : new NextRequest(request);
  const params = req.nextUrl.searchParams;

  // Hosted UI가 사용자 취소·설정 오류를 error로 알려준다.
  const hostedUiError = params.get("error");
  if (hostedUiError) return toLogin(hostedUiError);

  const code = params.get("code");
  const state = params.get("state");
  const expectedState = req.cookies.get(STATE_COOKIE)?.value;
  const verifier = req.cookies.get(VERIFIER_COOKIE)?.value;

  // CSRF 방어: 공격자가 유도한 콜백은 우리가 심은 state와 맞지 않는다.
  // verifier가 없으면(쿠키 만료·다른 브라우저) 교환 자체가 불가능하다.
  if (!code || !state || !expectedState || state !== expectedState || !verifier) {
    return toLogin("state_mismatch");
  }

  let tokens;
  try {
    tokens = await exchangeCode(cognitoEnv(), code, verifier);
  } catch (err) {
    // 상세 사유는 서버 로그에만 — 사용자에게는 일반화된 오류를 보여준다.
    // TokenExchangeError의 message는 세 가지 실패 형태(HTTP 오류·비JSON
    // 응답·토큰 누락) 중 어떤 것이었는지 담고 있으므로, 이걸 버리면 디버깅에
    // 아무 정보도 남지 않는다.
    const reason = err instanceof Error ? err.message : String(err);
    console.error(`authorization code exchange failed: ${reason}`);
    return toLogin("exchange_failed");
  }

  const next = safeNext(req.cookies.get(NEXT_COOKIE)?.value, req.url);
  // safeNext가 이미 same-origin 상대 경로를 보장한다(오픈 리다이렉트 방어).
  const res = redirectTo(next, 302);

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
