// frontend/lib/auth/redirectTo.ts
//
// 앱 내부로 보내는 리다이렉트의 유일한 생성 지점.
//
// 왜 NextResponse.redirect(new URL(path, req.url))를 쓰지 않는가:
// Next 15는 미들웨어/route handler의 req.url을 Host 헤더가 아니라 서버가 아는
// 자체 origin으로 조립한다. 그래서 프록시(nginx) + CloudFront 뒤에서는 내부
// 주소가 Location으로 새어 나간다 — 실측 배포 버그: CloudFront로 접속하면
// `https://localhost:3000/login?next=%2F`로 리다이렉트됐다. EC2에서 Host를
// CloudFront 도메인으로 줘도, X-Forwarded-Host를 줘도 결과가 같았다(nginx는
// 무죄였고, next start의 `-H 127.0.0.1` 바인딩이 origin의 출처였다).
//
// 상대 Location은 브라우저가 현재 오리진 기준으로 해석하므로 CloudFront ·
// 리버스 프록시 · 로컬 dev 모두에서 맞는다. RFC 7231부터 Location에 절대 URL
// 요구가 사라져 상대값이 정식이며, 프록시 뒤 배포에서는 이게 유일하게 옳은
// 형태다. 외부(Cognito Hosted UI)로 나가는 리다이렉트는 절대 URL이어야 하므로
// 이 헬퍼를 쓰지 않는다 — 그건 cognitoUrls.ts가 APP_BASE_URL로 조립한다.
import { NextResponse } from "next/server";

/**
 * 앱 내부 경로로 리다이렉트한다. `path`는 반드시 "/"로 시작하는 상대 경로다.
 *
 * NextResponse.redirect()는 URL 파싱을 강제해 상대값을 거부하므로(“URL is
 * malformed”) 응답을 직접 만든다.
 */
export function redirectTo(path: string, status: 302 | 307 = 307): NextResponse {
  // 오픈 리다이렉트 방어: protocol-relative("//evil.example")나 절대 URL이
  // 흘러들면 상대값인 척하면서 오프사이트로 튄다. 호출자가 safeNext를 거친
  // 값을 넘기는 게 원칙이지만, 이 지점에서도 불변식을 지킨다.
  const safe = path.startsWith("/") && !path.startsWith("//") ? path : "/";
  return new NextResponse(null, { status, headers: { location: safe } });
}

/** `/login`으로 보내며 사유를 쿼리로 싣는다(로그인 화면이 한국어로 번역한다). */
export function redirectToLogin(reason?: string, status: 302 | 307 = 307): NextResponse {
  const path = reason ? `/login?error=${encodeURIComponent(reason)}` : "/login";
  return redirectTo(path, status);
}
