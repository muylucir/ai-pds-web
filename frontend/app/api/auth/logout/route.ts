// frontend/app/api/auth/logout/route.ts
//
// 쿠키를 지우고 Cognito 세션도 끊는다. 쿠키만 지우면 Hosted UI에 남은 세션
// 때문에 다음 로그인이 비밀번호를 묻지 않고 곧바로 통과한다(공용 PC 문제).
import { NextRequest, NextResponse } from "next/server";
import { cognitoEnv, logoutUrl } from "@/lib/auth/cognitoUrls";
import { redirectToLogin } from "@/lib/auth/redirectTo";
import {
  ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, clearedCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// **303 See Other.** 이 숫자가 이 파일에서 가장 중요한 것이다.
//
// 로그아웃 버튼은 POST로 온다(아래 POST 핸들러 주석 참고). 303은 스펙상
// 브라우저가 리다이렉트를 **GET으로** 따라가게 만드는 유일한 코드다. 307/308은
// 메서드를 보존하고, Next의 `NextResponse.redirect()` 기본값이 바로 307이다
// (next/dist/server/web/spec-extension/response.js: `?? 307`).
//
// 그 기본값이 실측 결함이었다(2026-08-04): 버튼의 POST가 Cognito Hosted UI의
// `/logout`으로 POST로 재발행되고, 그 엔드포인트는 GET만 받으므로 405
// (Method Not Allowed)가 떴다 — 로그아웃이 되지 않았다.
//
// 이 실패는 **우리 로그에 아무것도 남기지 않는다.** 우리 응답은 정상적인 307
// 이었고 405는 Cognito가 브라우저에 직접 낸 것이므로, 증거는 브라우저 콘솔에만
// 있었다. 그래서 숫자를 기본값에 맡기지 않고 명시한다.
//
// 302도 실무상 GET으로 바뀌지만 그건 역사적 관용이고 스펙이 보장하지 않는다.
// 303은 "메서드를 GET으로 바꿔 따라가라"가 정의 자체다.
const SEE_OTHER = 303;

function clearAll(res: NextResponse): NextResponse {
  for (const name of [ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}

// req는 쓰지 않는다(리다이렉트가 상대 경로가 되어 req.url이 필요 없어졌다).
// 시그니처는 Next의 route handler 계약이므로 이름만 밑줄로 표시하고 남긴다.
export async function GET(_req: NextRequest) {
  const env = cognitoEnv();
  if (!env.domain || !env.clientId) {
    // 상대 Location(lib/auth/redirectTo.ts). Hosted UI logoutUrl은 외부
    // 절대 URL이어야 하므로 아래는 그대로 둔다.
    //
    // 이 경로도 302가 아니라 303이다 — POST로 들어온 로그아웃이 여기로 떨어질 수
    // 있고, `/login`은 페이지(GET)다. redirectToLogin의 기본값(307)을 그대로
    // 쓰면 로컬/바이패스 환경에서 같은 결함이 재현된다.
    return clearAll(redirectToLogin(undefined, SEE_OTHER));
  }
  return clearAll(NextResponse.redirect(logoutUrl(env), SEE_OTHER));
}

// 헤더의 로그아웃 버튼이 POST로 부를 수 있게 한다(GET 로그아웃은 프리페치에
// 걸려 의도치 않게 세션을 끊을 수 있다).
export async function POST(req: NextRequest) {
  return GET(req);
}
