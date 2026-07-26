import { describe, expect, it } from "vitest";
import { safeNext } from "./safeNext";

// login과 callback 두 곳에서 이 검증을 쓴다. 요청은 항상 우리 앱의 origin에서
// 오므로(req.url), 실제 origin은 늘 https://app.example.com 이다.
const REQUEST_URL = "https://app.example.com/api/auth/callback?code=1&state=2";

describe("safeNext", () => {
  it("preserves an internal path", () => {
    expect(safeNext("/ok/path", REQUEST_URL)).toBe("/ok/path");
  });

  it("preserves query and hash on an internal path", () => {
    expect(safeNext("/projects/p1/dashboard?tab=x#frag", REQUEST_URL))
      .toBe("/projects/p1/dashboard?tab=x#frag");
  });

  it("falls back to / for an empty string", () => {
    expect(safeNext("", REQUEST_URL)).toBe("/");
  });

  it("falls back to / for undefined", () => {
    expect(safeNext(undefined, REQUEST_URL)).toBe("/");
  });

  it("falls back to / for null", () => {
    expect(safeNext(null, REQUEST_URL)).toBe("/");
  });

  // 회귀: 이전에 막혔던 경로들이 여전히 막혀야 한다.
  it("rejects a protocol-relative path (//evil.example)", () => {
    expect(safeNext("//evil.example", REQUEST_URL)).toBe("/");
  });

  it("rejects an absolute off-site URL", () => {
    expect(safeNext("https://evil.example/x", REQUEST_URL)).toBe("/");
  });

  it("rejects a javascript: URL", () => {
    expect(safeNext("javascript:alert(1)", REQUEST_URL)).toBe("/");
  });

  // 실제 취약점: "/\evil.example" — 슬래시 하나 + 백슬래시. WHATWG URL 파서는
  // 특수 스킴에서 백슬래시를 슬래시처럼 취급하므로 문자열 프리픽스 검사
  // ("//"로 시작하는지만 보는)는 이걸 통과시키고, 브라우저에서는 실제로
  // evil.example로 resolve된다. origin 비교라야 이걸 잡는다.
  it("rejects a leading-backslash bypass (/\\evil.example)", () => {
    expect(safeNext("/\\evil.example", REQUEST_URL)).toBe("/");
  });

  it("rejects a slash-then-backslash bypass (/\\/evil.example)", () => {
    expect(safeNext("/\\/evil.example", REQUEST_URL)).toBe("/");
  });

  it("rejects a backslash-t bypass (/\\tevil)", () => {
    expect(safeNext("/\\tevil", REQUEST_URL)).toBe("/");
  });

  // 백슬래시가 우리 자신의 origin 안에 머무르는 경우까지도 — 우리는 백슬래시가
  // 든 경로를 스스로 생성하지 않으므로, 파서가 그걸 어떻게 다루는지에 기대지
  // 않고 통째로 거부한다(예측 가능성을 위한 의도적 결정 — origin이 우연히
  // 같더라도 예외를 두지 않는다).
  it("rejects any path containing a backslash, even if same-origin after resolution", () => {
    expect(safeNext("/ok/pa\\th", REQUEST_URL)).toBe("/");
  });

  // 실제 취약점(2차 발견): raw가 우리 자신의 origin을 절대 URL로 명시하면
  // origin 비교는 통과하지만, pathname 자체가 "//"로 시작할 수 있다
  // ("https://app.example.com//evil.example" -> pathname "//evil.example").
  // 그 문자열을 반환하면 함수의 후조건(반환값은 다시 resolve해도 항상
  // 우리 origin)이 깨진다 — 나중에 이 값을 또 new URL(value, url)에 넣는
  // 호출자가 생기면 오프사이트로 튄다.
  it("rejects a same-origin absolute URL whose path itself starts with //", () => {
    expect(safeNext("https://app.example.com//evil.example", REQUEST_URL)).toBe("/");
  });

  it("rejects a same-origin absolute URL with a bare // path", () => {
    expect(safeNext("https://app.example.com//", REQUEST_URL)).toBe("/");
  });

  it("preserves a legitimate deep link with query and hash (regression)", () => {
    expect(safeNext("/projects/p1/dashboard?tab=x#frag", REQUEST_URL))
      .toBe("/projects/p1/dashboard?tab=x#frag");
  });

  // 후조건 자체를 테스트한다: 모든 입력에 대해, safeNext의 반환값을 다시
  // resolve했을 때 항상 우리 자신의 origin으로 떨어져야 한다. 이 하나의
  // 불변식이 지켜지면, 앞으로 나올 새로운 인코딩 트릭도 개별 케이스를
  // 추가하기 전에 이 테스트가 잡아낸다.
  it("always returns a value that re-resolves to our own origin (postcondition)", () => {
    const ourOrigin = new URL(REQUEST_URL).origin;
    const candidates = [
      "/ok/path",
      "/projects/p1/dashboard?tab=x#frag",
      "",
      undefined,
      null,
      "//evil.example",
      "https://evil.example/x",
      "javascript:alert(1)",
      "/\\evil.example",
      "/\\/evil.example",
      "/\\tevil",
      "/ok/pa\\th",
      "https://app.example.com//evil.example",
      "https://app.example.com//",
    ];
    for (const c of candidates) {
      const out = safeNext(c, REQUEST_URL);
      const reresolved = new URL(out, REQUEST_URL);
      expect(reresolved.origin).toBe(ourOrigin);
    }
  });
});
