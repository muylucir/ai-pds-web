import { describe, expect, it } from "vitest";
import { challengeFor, randomUrlSafe } from "./pkce";

describe("randomUrlSafe", () => {
  it("produces URL-safe strings with no padding", () => {
    const v = randomUrlSafe(32);
    // base64url: must have no +/= to be safe as a query parameter.
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
    // RFC 7636 Appendix B's test vector — checks that our implementation produces the same value as the
    // standard. If this is wrong, Cognito answers only with invalid_grant, making the cause hard to find.
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
