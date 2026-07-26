import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ACCESS_COOKIE, clearedCookieOptions, ID_COOKIE, NEXT_COOKIE, REFRESH_COOKIE,
  sessionCookieOptions, STATE_COOKIE, transientCookieOptions, VERIFIER_COOKIE,
} from "./cookies";

afterEach(() => {
  // 다른 테스트 파일로 상태가 새지 않도록 매번 복원한다.
  vi.unstubAllEnvs();
});

describe("sessionCookieOptions", () => {
  it("returns httpOnly, sameSite=lax, path=/, and the given maxAge", () => {
    const opts = sessionCookieOptions(3600);
    expect(opts.httpOnly).toBe(true);
    // strict가 아닌 이유: Hosted UI에서 돌아오는 top-level 리다이렉트에 쿠키가
    // 실려야 한다. strict면 콜백 직후 요청에서 쿠키가 빠져 로그인이 무한
    // 루프한다 — 이 값이 바뀌면 여기서 잡혀야 한다.
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
    // PKCE verifier / state / next: 로그인 왕복(최대 10분)만 살아 있으면 된다.
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
    // route handler·미들웨어·프록시가 모두 같은 문자열을 참조한다 — 오타 하나가
    // 세션을 조용히 깨뜨린다.
    expect(ACCESS_COOKIE).toBe("pf_access");
    expect(ID_COOKIE).toBe("pf_id");
    expect(REFRESH_COOKIE).toBe("pf_refresh");
    expect(VERIFIER_COOKIE).toBe("pf_pkce");
    expect(STATE_COOKIE).toBe("pf_state");
    expect(NEXT_COOKIE).toBe("pf_next");
  });

  it("are all distinct", () => {
    const names = [
      ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, VERIFIER_COOKIE, STATE_COOKIE,
      NEXT_COOKIE,
    ];
    expect(new Set(names).size).toBe(names.length);
  });
});
