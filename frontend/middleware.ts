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
import { redirectTo } from "@/lib/auth/redirectTo";

export function middleware(req: NextRequest) {
  const decision = gateDecision(req.nextUrl.pathname,
                                req.cookies.get(ACCESS_COOKIE)?.value);
  if (decision.kind === "allow") return NextResponse.next();
  // Location은 상대 경로여야 한다 — 이유는 lib/auth/redirectTo.ts 참조
  // (프록시/CloudFront 뒤에서 req.url이 내부 주소를 새게 만든다).
  if (decision.kind === "home") return redirectTo("/");
  return redirectTo(`/login?next=${encodeURIComponent(decision.next)}`);
}

export const config = {
  // 정적 자산과 파비콘은 판정 대상이 아니다(매 요청 미들웨어 실행은 낭비다).
  // 공개 경로 판정은 gate.ts가 하므로 여기서 제외하지 않는다 — 목록이 두 곳에
  // 흩어지면 어긋난다.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
