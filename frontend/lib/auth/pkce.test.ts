import { describe, expect, it } from "vitest";
import { challengeFor, randomUrlSafe } from "./pkce";

describe("randomUrlSafe", () => {
  it("produces URL-safe strings with no padding", () => {
    const v = randomUrlSafe(32);
    // base64url: +/= 가 없어야 쿼리 파라미터로 안전하다.
    expect(v).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(v.length).toBeGreaterThanOrEqual(43);
  });

  it("does not repeat", () => {
    const seen = new Set(Array.from({ length: 50 }, () => randomUrlSafe()));
    expect(seen.size).toBe(50);
  });
});

describe("challengeFor", () => {
  it("computes the S256 challenge from a known verifier", async () => {
    // RFC 7636 Appendix B의 검증 벡터 — 우리 구현이 표준과 같은 값을 내는지
    // 확인한다. 여기가 틀리면 Cognito가 invalid_grant로만 답해 원인 파악이 어렵다.
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    await expect(challengeFor(verifier))
      .resolves.toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("is deterministic", async () => {
    const v = randomUrlSafe();
    expect(await challengeFor(v)).toBe(await challengeFor(v));
  });

  it("differs for different verifiers", async () => {
    expect(await challengeFor(randomUrlSafe()))
      .not.toBe(await challengeFor(randomUrlSafe()));
  });
});
