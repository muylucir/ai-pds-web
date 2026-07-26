// frontend/middleware.ts
//
// 로그인하지 않은 브라우저를 /login으로 보내고, pm이 관리 화면 URL을 직접 치면
// 되돌린다. 판정은 lib/auth/gate.ts가 하고 여기서는 요청/응답만 다룬다.
//
// ⚠️ 보안 경계가 아니다 — 쿠키 서명을 검증하지 않는다. 실제 권한 판단은 백엔드의
// require_user / require_admin이며, 위조 쿠키로 화면을 열어도 API가 전부 막힌다.
import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE } from "@/lib/auth/cookies";
import { gateDecision } from "@/lib/auth/gate";

// 리다이렉트 대상 origin은 **Host 헤더**에서 얻는다. req.url이 아니다.
//
// 왜 req.url이 아닌가: Next 15는 req.url을 Host 헤더가 아니라 서버가 아는 자체
// origin으로 조립한다. next start가 `-H 127.0.0.1`로 바인딩되어 있으므로
// `new URL(path, req.url)`은 프록시/CloudFront 뒤에서 항상 내부 주소를 만든다 —
// 실측 배포 버그: CloudFront로 접속하면 Location이
// `https://localhost:3000/login?next=%2F`이었다.
//
// 왜 상대 경로가 아닌가: 미들웨어는 Edge 런타임에서 돌고, 그 런타임이 응답의
// location 헤더를 내부적으로 new URL()로 파싱한다. 상대값을 주면
// `TypeError: Invalid URL, input: '/login?next=%2F'`로 모든 페이지가 500이
// 된다(실측). route handler(Node 런타임)는 상대값이 통하지만 여기서는 안 된다.
//
// Host는 사용자가 조작할 수 있는 값이지만 여기서는 안전하다: 이 값으로 만드는
// URL은 same-path 리다이렉트일 뿐이고(경로는 우리가 정한다), 권한 판단에는
// 쓰이지 않는다. 프록시 뒤에서는 nginx가 실제 접속 호스트를 그대로 전달한다
// (proxy_set_header Host $host).
function selfUrl(req: NextRequest, path: string): string {
  const host = req.headers.get("host");
  // x-forwarded-proto가 있으면 그것이 사용자가 쓴 스킴이다(nginx가 https를
  // 넣는다). 없으면 요청 스킴 — 로컬 http dev를 https로 강제하지 않는다.
  const proto = req.headers.get("x-forwarded-proto")?.split(",")[0].trim()
    || req.nextUrl.protocol.replace(":", "");
  if (host) {
    try {
      // Host가 조작·손상된 경우(파싱 실패)에는 아래 폴백으로 떨어진다.
      // 미들웨어가 예외를 던지면 모든 페이지가 500이 되므로 절대 던지지 않는다.
      return new URL(path, `${proto}://${host}`).toString();
    } catch {
      // fall through
    }
  }
  return new URL(path, req.url).toString();
}

export function middleware(req: NextRequest) {
  const decision = gateDecision(req.nextUrl.pathname,
                                req.cookies.get(ACCESS_COOKIE)?.value);
  if (decision.kind === "allow") return NextResponse.next();
  if (decision.kind === "home") {
    return NextResponse.redirect(selfUrl(req, "/"));
  }
  return NextResponse.redirect(
    selfUrl(req, `/login?next=${encodeURIComponent(decision.next)}`));
}

export const config = {
  // 정적 자산과 파비콘은 판정 대상이 아니다(매 요청 미들웨어 실행은 낭비다).
  // 공개 경로 판정은 gate.ts가 하므로 여기서 제외하지 않는다 — 목록이 두 곳에
  // 흩어지면 어긋난다.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
