import { describe, expect, it } from "vitest";
import { isRetryableWithRefresh, withBearer } from "./proxyAuth";

describe("withBearer", () => {
  it("adds the bearer header from the cookie value", () => {
    const out = withBearer(new Headers({ accept: "application/json" }), "tok-1");
    expect(out.get("authorization")).toBe("Bearer tok-1");
    expect(out.get("accept")).toBe("application/json");
  });

  it("strips the Cookie header so session cookies never reach the backend", () => {
    // 백엔드는 쿠키를 모른다. 흘려보내면 세션 토큰이 불필요하게 한 계층 더
    // 노출되고, 로그에 남을 수도 있다.
    const out = withBearer(
      new Headers({ cookie: "pf_access=secret; pf_refresh=alsosecret" }), "tok-1");
    expect(out.get("cookie")).toBeNull();
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
