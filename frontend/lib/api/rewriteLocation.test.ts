import { describe, it, expect } from "vitest";
import { rewriteLocation } from "./rewriteLocation";

const BACKEND = "http://localhost:8000";

describe("rewriteLocation", () => {
  it("re-anchors an absolute backend redirect under /api", () => {
    // Starlette's trailing-slash 307 names its own origin; passed through
    // verbatim the browser leaves the public host and hangs.
    expect(rewriteLocation(`${BACKEND}/proto/p1/demo/`, BACKEND)).toBe(
      "/api/proto/p1/demo/",
    );
  });

  it("preserves query and hash", () => {
    expect(rewriteLocation(`${BACKEND}/proto/p1/demo/x?a=1#top`, BACKEND)).toBe(
      "/api/proto/p1/demo/x?a=1#top",
    );
  });

  it("prefixes a bare absolute path", () => {
    expect(rewriteLocation("/proto/p1/demo/next", BACKEND)).toBe(
      "/api/proto/p1/demo/next",
    );
  });

  it("does not double-prefix an already anchored path", () => {
    expect(rewriteLocation("/api/proto/p1/demo/", BACKEND)).toBe(
      "/api/proto/p1/demo/",
    );
  });

  it("leaves a relative target for the browser to resolve", () => {
    expect(rewriteLocation("next-page", BACKEND)).toBe("next-page");
  });

  it("leaves an external redirect untouched", () => {
    const ext = "https://accounts.google.com/o/oauth2/auth?x=1";
    expect(rewriteLocation(ext, BACKEND)).toBe(ext);
  });
});
