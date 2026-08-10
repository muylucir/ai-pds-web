// frontend/app/api/auth/refresh/route.ts
//
// access/id 토큰을 **선제적으로** 갱신한다. 요청이 401로 실패하기를 기다리지
// 않는 것이 이 라우트의 존재 이유다.
//
// **왜 필요한가(프로토타입 빌드 중 로그아웃).** /api 프록시의 갱신은 백엔드가
// 401을 냈을 때만 발동한다(app/api/[...path]/route.ts의 isRetryableWithRefresh).
// 긴 SSE 스트림은 그 조건에 영원히 걸리지 않는다: `GET /events`가 200으로
// 열리고 나면 그 연결에서 401이 다시 올 일이 없으므로, 스트림이 도는 수십 분
// 동안 갱신 기회가 하나도 없다. access 토큰은 60분이고 프로토타입 빌드는 그에
// 육박하므로, 빌드가 끝난 뒤 첫 요청에서 세션이 만료된 채로 발견된다.
//
// 짧은 턴이 반복되는 디스커버리 채팅에서는 매 턴이 갱신 기회가 되어 이 결함이
// 드러나지 않았다 — 그래서 프로토타입에서만 보였다.
//
// `/api/auth/me`로 대신할 수 없다: 그 라우트는 쿠키의 **존재**만 확인하고
// 갱신하지 않으며, /api 프록시를 타지도 않으므로 프록시의 갱신 경로와도 무관하다.
//
// 클라이언트 쪽 주기 호출은 lib/auth/keepSessionAlive.ts가 담당한다.
import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, sessionCookieOptions } from "@/lib/auth/cookies";
import { cognitoEnv } from "@/lib/auth/cognitoUrls";
import { refreshTokens } from "@/lib/auth/tokenExchange";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  // 테스트는 NextRequest 대신 평범한 Request를 넘긴다(Next 런타임 밖에서는
  // cookies 게터가 없다) — 실제 요청은 이미 NextRequest이므로 no-op.
  // callback/route.ts가 같은 규약을 쓴다.
  const req = request instanceof NextRequest ? request : new NextRequest(request);
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  if (!refresh) {
    // 리프레시 토큰이 없으면 갱신할 것이 없다. Cognito를 때리지 않는다 —
    // 실패가 확실한 호출이고, 폴링 경로라서 그 낭비가 반복된다.
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }

  let tokens;
  try {
    tokens = await refreshTokens(cognitoEnv(), refresh);
  } catch (err) {
    // 리프레시 토큰이 만료(30일)·폐기됐다. 여기서 200을 내면 클라이언트가
    // 세션이 살아 있다고 오판해 계속 폴링하고, 사용자는 다음 실제 요청에서야
    // 로그아웃을 발견한다 — 이 라우트가 막으려던 그 실패 모양이다.
    const reason = err instanceof Error ? err.message : String(err);
    console.error(`proactive token refresh failed: ${reason}`);
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }

  const res = NextResponse.json({ authenticated: true });
  const session = sessionCookieOptions(tokens.expires_in);
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, session);
  res.cookies.set(ID_COOKIE, tokens.id_token, session);
  // REFRESH_COOKIE는 건드리지 않는다. 이 풀은 리프레시 로테이션이 꺼져 있어
  // (infra/lib/auth-client-config.ts — refreshTokenRotationGracePeriod 미지정)
  // refresh 그랜트가 새 refresh_token을 주지 않으므로, 응답에 없는 값으로
  // 덮어쓰면 30일 창이 통째로 사라진다.
  return res;
}
