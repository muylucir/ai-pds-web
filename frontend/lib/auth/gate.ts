// frontend/lib/auth/gate.ts
//
// 미들웨어의 판정 로직. middleware.ts에서 분리한 이유는 이 목록이 이 프로젝트에서
// 가장 실수하기 쉬운 부분이라 직접 단정하고 싶기 때문이다.
//
// ⚠️ 이것은 UX 게이트다. 쿠키의 서명을 검증하지 않으므로 위조된 쿠키로 /admin
// 화면을 열 수 있지만, 그 화면이 부르는 모든 API가 백엔드에서 403으로 막힌다.
// 보안 경계는 백엔드의 require_admin이다.
import { decodeJwtPayload, roleFromClaims } from "./claims";

export type GateDecision =
  | { kind: "allow" }
  | { kind: "login"; next: string }
  | { kind: "home" };

// 로그인 없이 접근해야 하는 경로. 백엔드의
// tests/test_auth_route_coverage.py::PUBLIC_PATHS와 대응한다.
//   /login       로그인 화면 자체
//   /survey/*    익명 설문 (계정 없는 최종 사용자)
//   /proto/*     프로토타입 프리뷰 (같은 사용자가 앱을 실제로 써본다)
//   /api/auth/*  로그인 왕복 자체
const PUBLIC_PREFIXES = ["/login", "/survey/", "/proto/", "/api/auth/"];

// /api/*는 통과시킨다: 프록시가 Bearer를 붙이고 백엔드가 판단한다. 여기서
// 리다이렉트하면 fetch가 HTML 로그인 페이지를 받아 JSON 파싱 오류로 깨진다.
const PASSTHROUGH_PREFIXES = ["/api/"];

function isPublic(pathname: string): boolean {
  if (pathname === "/login") return true;
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))
    || PASSTHROUGH_PREFIXES.some((p) => pathname.startsWith(p));
}

export function gateDecision(pathname: string,
                             accessToken: string | undefined): GateDecision {
  if (isPublic(pathname)) return { kind: "allow" };
  if (!accessToken) return { kind: "login", next: pathname };

  if (pathname.startsWith("/admin")) {
    // 역할을 확인할 수 없으면(쿠키 손상·그룹 없음) 관리 화면을 열지 않는다.
    if (roleFromClaims(decodeJwtPayload(accessToken)) !== "admin") {
      return { kind: "home" };
    }
  }
  // 쿠키가 있으면 통과시킨다. 만료된 쿠키를 여기서 되돌리면 백엔드의 리프레시
  // 경로를 타지 못하고 무한 왕복이 된다 — 401은 프론트가 처리한다.
  return { kind: "allow" };
}
