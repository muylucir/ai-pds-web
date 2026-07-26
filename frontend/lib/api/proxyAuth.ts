// frontend/lib/api/proxyAuth.ts
//
// /api 프록시의 인증 부분. route 파일이 헬퍼를 export할 수 없어 분리한다
// (rewriteLocation.ts와 같은 이유).

// 요청 본문을 재생할 수 없는 메서드 — 401 리프레시 후 재시도가 불가능하다.
// (init.body가 스트림이면 한 번 소비된 뒤 되돌릴 수 없다.)
const REPLAYABLE_METHODS = new Set(["GET", "HEAD", "DELETE"]);

export function withBearer(headers: Headers,
                           accessToken: string | undefined): Headers {
  const out = new Headers(headers);
  // 백엔드는 쿠키를 모른다. 흘려보내면 세션 토큰이 한 계층 더 노출된다.
  out.delete("cookie");
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
