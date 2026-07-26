import { describe, expect, it } from "vitest";
import * as authModule from "./auth";
import { CREDENTIALS } from "./auth";

describe("auth seam", () => {
  it("exposes credentials:include for cookie-based auth", () => {
    // 토큰은 httpOnly 쿠키에 있고 JS가 읽을 수 없다. 클라이언트가 할 일은
    // 쿠키를 요청에 실으라고 fetch에 알리는 것뿐이다.
    expect(CREDENTIALS).toBe("include");
  });

  it("no longer exposes getAuthToken", () => {
    // 이 함수가 남아 있으면 새 호출부가 undefined 토큰을 헤더에 붙이려 시도한다.
    expect("getAuthToken" in authModule).toBe(false);
  });
});
