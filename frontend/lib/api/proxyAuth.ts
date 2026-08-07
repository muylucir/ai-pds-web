// frontend/lib/api/proxyAuth.ts
//
// /api 프록시의 인증 부분. route 파일이 헬퍼를 export할 수 없어 분리한다
// (rewriteLocation.ts와 같은 이유).

// 요청 본문을 재생할 수 없는 메서드 — 401 리프레시 후 재시도가 불가능하다.
// (init.body가 스트림이면 한 번 소비된 뒤 되돌릴 수 없다.)
const REPLAYABLE_METHODS = new Set(["GET", "HEAD", "DELETE"]);

//: 백엔드로 흘려보내도 되는 쿠키의 접두어. 프로토타입 접근 토큰 쿠키다
//: (backend/pathfinder/routes/proto_public.py의 COOKIE_PREFIX) — 양쪽이 같은
//: 문자열이어야 하고, 어긋나면 쿠키가 백엔드에 닿지 않아 모든 프로토타입
//: 프리뷰가 404가 된다.
const PROTO_COOKIE_PREFIX = "pf_proto_";

/** 백엔드로 보낼 수 있는 쿠키만 남긴 Cookie 헤더 값. 없으면 null.
 *
 *  **허용목록이다**(차단목록이 아니다). 이 방향이 load-bearing인 이유: 나중에
 *  세션 관련 쿠키가 하나 더 생겼을 때 차단목록이라면 그것을 여기에 추가하는
 *  것을 잊는 순간 조용히 백엔드로 새고, 그 사실은 아무 테스트도 깨뜨리지
 *  않는다. 허용목록에서는 그 실수가 "새 쿠키가 전달되지 않는다"로 나타나
 *  기능이 동작하지 않는 쪽으로 실패한다.
 *
 *  프로토타입 접근 쿠키만 통과시키는 이유는 그것이 **백엔드가 판단해야 하는
 *  유일한 쿠키**이기 때문이다. 세션 JWT(pf_access/pf_id/pf_refresh)는 이
 *  경계에서 멈추고 withBearer가 Authorization으로 번역한다 — 백엔드는 세션
 *  쿠키를 읽지 않는다. */
export function forwardableCookies(cookieHeader: string | null | undefined): string | null {
  if (!cookieHeader) return null;
  const kept = cookieHeader
    .split(";")
    .map((c) => c.trim())
    // 이름으로 판정한다. 접두어 검사이므로 값을 분리할 필요가 없다 — 쿠키 이름에는
    // "="가 들어갈 수 없으므로 `startsWith`가 이름의 시작만 본다는 것이 보장된다.
    .filter((c) => c.startsWith(PROTO_COOKIE_PREFIX));
  return kept.length > 0 ? kept.join("; ") : null;
}

export function withBearer(headers: Headers,
                           accessToken: string | undefined): Headers {
  const out = new Headers(headers);
  // 백엔드는 세션 쿠키를 모른다. 흘려보내면 세션 토큰이 한 계층 더 노출된다.
  // 전부 지운 뒤 허용목록만 되살린다 — 지우는 것이 기본값이어야 한다.
  const forwardable = forwardableCookies(out.get("cookie"));
  out.delete("cookie");
  // 프로토타입 접근 쿠키는 백엔드가 판단하는 유일한 쿠키다
  // (routes/proto_public.py의 _authorized). 이것이 없으면 /api/proto/* 요청이
  // 전부 404가 된다 — 그 경로도 이 프록시를 통과하기 때문이다.
  if (forwardable) out.set("cookie", forwardable);
  // 클라이언트가 보낸 Authorization은 신뢰하지 않는다 — httpOnly 쿠키가 진실이다.
  out.delete("authorization");
  if (accessToken) out.set("authorization", `Bearer ${accessToken}`);
  return out;
}

export function isRetryableWithRefresh(status: number, method: string,
                                       hasRefresh: boolean): boolean {
  return status === 401 && hasRefresh
    && REPLAYABLE_METHODS.has(method.toUpperCase());
}
