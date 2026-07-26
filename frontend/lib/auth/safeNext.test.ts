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
});
