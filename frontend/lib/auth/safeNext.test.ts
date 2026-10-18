import { describe, expect, it } from "vitest";
import { safeNext } from "./safeNext";

// Both login and callback use this check. Requests always come from our app's origin
// (req.url), so the real origin is always https://app.example.com.
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

  // Regression: paths that were blocked before must still be blocked.
  it("rejects a protocol-relative path (//evil.example)", () => {
    expect(safeNext("//evil.example", REQUEST_URL)).toBe("/");
  });

  it("rejects an absolute off-site URL", () => {
    expect(safeNext("https://evil.example/x", REQUEST_URL)).toBe("/");
  });

  it("rejects a javascript: URL", () => {
    expect(safeNext("javascript:alert(1)", REQUEST_URL)).toBe("/");
  });

  // A real vulnerability: "/\evil.example" — one slash + a backslash. The WHATWG URL parser
  // treats a backslash like a slash in special schemes, so a string prefix check
  // (one that only checks for a leading "//") lets this through, and in the browser it actually
  // resolves to evil.example. Only an origin comparison catches it.
  it("rejects a leading-backslash bypass (/\\evil.example)", () => {
    expect(safeNext("/\\evil.example", REQUEST_URL)).toBe("/");
  });

  it("rejects a slash-then-backslash bypass (/\\/evil.example)", () => {
    expect(safeNext("/\\/evil.example", REQUEST_URL)).toBe("/");
  });

  it("rejects a backslash-t bypass (/\\tevil)", () => {
    expect(safeNext("/\\tevil", REQUEST_URL)).toBe("/");
  });

  // Even when the backslash stays within our own origin — we never generate paths containing a
  // backslash ourselves, so rather than rely on how the parser handles it, the whole thing is
  // rejected (a deliberate decision for predictability — no exception even if the origin
  // happens to be the same).
  it("rejects any path containing a backslash, even if same-origin after resolution", () => {
    expect(safeNext("/ok/pa\\th", REQUEST_URL)).toBe("/");
  });

  // A real vulnerability (second finding): if raw spells out our own origin as an absolute URL,
  // the origin comparison passes, but the pathname itself can start with "//"
  // ("https://app.example.com//evil.example" -> pathname "//evil.example").
  // Returning that string breaks the function's postcondition (the return value, resolved again, is always
  // our origin) — if a caller ever appears that feeds this value into new URL(value, url) again,
  // it bounces offsite.
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

  // Tests the postcondition itself: for every input, resolving safeNext's return value again
  // must always land on our own origin. If this one invariant holds, this test
  // catches new encoding tricks that come along before individual cases
  // are added.
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
