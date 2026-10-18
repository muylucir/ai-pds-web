import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ACCESS_COOKIE, clearedCookieOptions, ID_COOKIE, NEXT_COOKIE, REFRESH_COOKIE,
  sessionCookieOptions, STATE_COOKIE, transientCookieOptions, VERIFIER_COOKIE,
} from "./cookies";

afterEach(() => {
  // Restore every time so state does not leak into other test files.
  vi.unstubAllEnvs();
});

describe("sessionCookieOptions", () => {
  it("returns httpOnly, sameSite=lax, path=/, and the given maxAge", () => {
    const opts = sessionCookieOptions(3600);
    expect(opts.httpOnly).toBe(true);
    // Why not strict: the cookie must be sent on the top-level redirect returning from the Hosted UI.
    // With strict, the cookie is dropped from the request right after the callback and login loops
    // forever — if this value changes, it must be caught here.
    expect(opts.sameSite).toBe("lax");
    expect(opts.path).toBe("/");
    expect(opts.maxAge).toBe(3600);
  });

  it("is not secure in development, so local http works", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(sessionCookieOptions(60).secure).toBe(false);
  });

  it("is secure in production", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(sessionCookieOptions(60).secure).toBe(true);
  });
});

describe("transientCookieOptions", () => {
  it("carries the same flags as a session cookie", () => {
    const opts = transientCookieOptions();
    expect(opts.httpOnly).toBe(true);
    expect(opts.sameSite).toBe("lax");
    expect(opts.path).toBe("/");
  });

  it("uses a short lifetime suitable for a login round-trip", () => {
    // PKCE verifier / state / next: they only need to live for the login round trip (at most 10 minutes).
    expect(transientCookieOptions().maxAge).toBe(600);
  });
});

describe("clearedCookieOptions", () => {
  it("sets maxAge to 0, the property that actually deletes the cookie", () => {
    expect(clearedCookieOptions().maxAge).toBe(0);
  });
});

describe("cookie name constants", () => {
  it("have the exact expected string values", () => {
    // The route handlers, middleware, and proxy all reference the same strings — a single typo
    // quietly breaks the session.
    expect(ACCESS_COOKIE).toBe("aipds_access");
    expect(ID_COOKIE).toBe("aipds_id");
    expect(REFRESH_COOKIE).toBe("aipds_refresh");
    expect(VERIFIER_COOKIE).toBe("aipds_pkce");
    expect(STATE_COOKIE).toBe("aipds_state");
    expect(NEXT_COOKIE).toBe("aipds_next");
  });

  it("are all distinct", () => {
    const names = [
      ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, VERIFIER_COOKIE, STATE_COOKIE,
      NEXT_COOKIE,
    ];
    expect(new Set(names).size).toBe(names.length);
  });
});
