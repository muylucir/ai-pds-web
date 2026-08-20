// frontend/lib/auth/cookies.ts
//
// 쿠키 이름과 속성의 단일 출처. route handler 3개와 프록시, 미들웨어가 같은
// 이름을 봐야 한다.

export const ACCESS_COOKIE = "aipds_access";
export const ID_COOKIE = "aipds_id";
export const REFRESH_COOKIE = "aipds_refresh";

// 로그인 왕복 중에만 존재하는 값 — 콜백에서 소비하고 즉시 지운다.
export const VERIFIER_COOKIE = "aipds_pkce";
export const STATE_COOKIE = "aipds_state";
export const NEXT_COOKIE = "aipds_next";

const isProd = () => process.env.NODE_ENV === "production";

export function sessionCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,        // JS가 토큰을 읽을 수 없다 — XSS로 탈취 불가
    secure: isProd(),      // 로컬 http 개발을 막지 않기 위해 프로덕션에서만
    // strict가 아닌 이유: Hosted UI에서 돌아오는 top-level 리다이렉트에 쿠키가
    // 실려야 한다. strict면 콜백 직후 요청에서 쿠키가 빠져 로그인이 무한 루프한다.
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

export function transientCookieOptions() {
  // PKCE verifier / state / next: 로그인 왕복(최대 10분)만 살아 있으면 된다.
  return { ...sessionCookieOptions(600) };
}

export function clearedCookieOptions() {
  return { ...sessionCookieOptions(0), maxAge: 0 };
}
