import { describe, expect, it } from "vitest";
import { forwardableCookies, isRetryableWithRefresh, withBearer } from "./proxyAuth";

describe("withBearer", () => {
  it("adds the bearer header from the cookie value", () => {
    const out = withBearer(new Headers({ accept: "application/json" }), "tok-1");
    expect(out.get("authorization")).toBe("Bearer tok-1");
    expect(out.get("accept")).toBe("application/json");
  });

  it("strips session cookies so they never reach the backend", () => {
    // 백엔드는 세션 쿠키를 모른다. 흘려보내면 세션 토큰이 불필요하게 한 계층 더
    // 노출되고, 로그에 남을 수도 있다.
    const out = withBearer(
      new Headers({ cookie: "pf_access=secret; pf_refresh=alsosecret" }), "tok-1");
    expect(out.get("cookie")).toBeNull();
  });

  it("forwards the prototype access cookie while still dropping session cookies", () => {
    // 이 조합이 핵심이다: 같은 Cookie 헤더 안에서 하나는 통과하고 나머지는
    // 막혀야 한다. 프로토타입 프리뷰(/api/proto/*)도 이 프록시를 통과하고,
    // 백엔드는 그 쿠키로 접근을 판정한다(routes/proto_public.py의 _authorized).
    const out = withBearer(
      new Headers({
        cookie: "pf_access=secret; aipds_proto_abc123=prototoken; pf_refresh=alsosecret",
      }),
      "tok-1");
    expect(out.get("cookie")).toBe("aipds_proto_abc123=prototoken");
    // 세션 토큰이 값에 섞여 나가지 않는 것을 문자열 수준에서 단정한다.
    expect(out.get("cookie")).not.toContain("secret");
  });

  it("sends no authorization header when there is no cookie", () => {
    // 인증이 꺼진 로컬 백엔드는 헤더 없이도 응답한다.
    const out = withBearer(new Headers(), undefined);
    expect(out.get("authorization")).toBeNull();
  });

  it("replaces a client-supplied authorization header", () => {
    // 클라이언트가 보낸 Authorization을 신뢰하지 않는다 — 쿠키가 진실이다.
    const out = withBearer(new Headers({ authorization: "Bearer forged" }), "tok-1");
    expect(out.get("authorization")).toBe("Bearer tok-1");
  });

  it("drops a client-supplied authorization header when there is no cookie", () => {
    const out = withBearer(new Headers({ authorization: "Bearer forged" }), undefined);
    expect(out.get("authorization")).toBeNull();
  });
});

describe("forwardableCookies", () => {
  it("keeps only aipds_proto_* cookies", () => {
    expect(forwardableCookies("aipds_proto_a=1; pf_access=2; aipds_proto_b=3"))
      .toBe("aipds_proto_a=1; aipds_proto_b=3");
  });

  it("returns null when nothing is forwardable", () => {
    // null이어야 한다(빈 문자열이 아니라): 호출부가 이 값으로 헤더를 심을지
    // 결정하므로, 빈 문자열이면 `Cookie: ` 라는 빈 헤더가 붙는다.
    expect(forwardableCookies("pf_access=1; pf_id=2; pf_refresh=3")).toBeNull();
  });

  it("handles a missing header", () => {
    expect(forwardableCookies(null)).toBeNull();
    expect(forwardableCookies(undefined)).toBeNull();
    expect(forwardableCookies("")).toBeNull();
  });

  it("preserves a value containing '=' padding", () => {
    // 토큰은 urlsafe base64 계열이라 값에 "="가 올 수 있다. 이름만 보고
    // 판정하므로 값이 잘리지 않아야 한다 — 잘리면 백엔드의 compare_digest가
    // 실패해 프리뷰가 404가 되고, 원인은 쿠키 파싱이라 찾기 어렵다.
    expect(forwardableCookies("aipds_proto_x=abc==")).toBe("aipds_proto_x=abc==");
  });

  it("does not fall for a session cookie whose VALUE mentions the prefix", () => {
    // 접두어 검사는 이름에만 걸려야 한다. 값에 있는 문자열로 통과하면
    // 공격자가 pf_access 값에 접두어를 심어 세션 토큰을 백엔드로 흘릴 수 있다.
    expect(forwardableCookies("pf_access=aipds_proto_nope")).toBeNull();
  });

  it("tolerates whitespace between cookie pairs", () => {
    expect(forwardableCookies("pf_access=1;aipds_proto_a=2;   aipds_proto_b=3"))
      .toBe("aipds_proto_a=2; aipds_proto_b=3");
  });
});

describe("isRetryableWithRefresh", () => {
  it("retries a GET on 401 when a refresh token exists", () => {
    expect(isRetryableWithRefresh(401, "GET", true)).toBe(true);
  });

  it("does not retry without a refresh token", () => {
    expect(isRetryableWithRefresh(401, "GET", false)).toBe(false);
  });

  it("does not retry non-401 responses", () => {
    for (const status of [200, 403, 404, 500, 502]) {
      expect(isRetryableWithRefresh(status, "GET", true)).toBe(false);
    }
  });

  it("does not retry methods that carry a streamed body", () => {
    // 요청 본문 스트림은 한 번 소비되면 되돌릴 수 없다 — 재시도하면 빈 본문이
    // 전송된다. GET/HEAD/DELETE만 안전하다.
    expect(isRetryableWithRefresh(401, "POST", true)).toBe(false);
    expect(isRetryableWithRefresh(401, "PUT", true)).toBe(false);
    expect(isRetryableWithRefresh(401, "GET", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "HEAD", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "DELETE", true)).toBe(true);
  });

  it("is case-insensitive about the method", () => {
    expect(isRetryableWithRefresh(401, "get", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "post", true)).toBe(false);
  });
});
