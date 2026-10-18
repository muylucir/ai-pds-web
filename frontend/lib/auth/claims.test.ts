import { describe, expect, it } from "vitest";
import {
  decodeJwtPayload, emailFromClaims, roleFromClaims,
} from "./claims";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256", kid: "k" })}.${b64(payload)}.fake-signature`;
}

describe("decodeJwtPayload", () => {
  it("reads the payload without verifying anything", () => {
    const c = decodeJwtPayload(fakeJwt({ sub: "s-1", email: "a@example.com" }));
    expect(c).toMatchObject({ sub: "s-1", email: "a@example.com" });
  });

  it("returns null for garbage instead of throwing", () => {
    // The middleware calls this — if it threw, every page would be a 500.
    expect(decodeJwtPayload("not-a-jwt")).toBeNull();
    expect(decodeJwtPayload("")).toBeNull();
    expect(decodeJwtPayload("a.b")).toBeNull();
    expect(decodeJwtPayload("a.!!!.c")).toBeNull();
  });

  it("handles base64url payloads that need padding", () => {
    const c = decodeJwtPayload(fakeJwt({ a: "x".repeat(5) }));
    // Check the value itself — not.toBeNull() alone would also pass a truncated or
    // wrongly decoded result.
    expect(c).toMatchObject({ a: "xxxxx" });
  });

  it("returns null for a bare JSON array payload", () => {
    // typeof is "object" for arrays too — Claims is an object contract, so arrays are rejected.
    const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
    const arrayJwt = `${b64({ alg: "RS256" })}.${b64([1, 2, 3])}.sig`;
    expect(decodeJwtPayload(arrayJwt)).toBeNull();
  });
});

describe("roleFromClaims", () => {
  it("reads cognito:groups", () => {
    expect(roleFromClaims({ "cognito:groups": ["admin"] })).toBe("admin");
    expect(roleFromClaims({ "cognito:groups": ["pm"] })).toBe("pm");
  });

  it("prefers admin when the user is in both groups", () => {
    // Must have the same priority as the backend verifier — otherwise the screen and permissions disagree.
    expect(roleFromClaims({ "cognito:groups": ["pm", "admin"] })).toBe("admin");
  });

  it("returns null when there is no known group", () => {
    expect(roleFromClaims({ "cognito:groups": [] })).toBeNull();
    expect(roleFromClaims({ "cognito:groups": ["other"] })).toBeNull();
    expect(roleFromClaims({})).toBeNull();
    expect(roleFromClaims(null)).toBeNull();
  });

  it("ignores a non-array groups claim", () => {
    expect(roleFromClaims({ "cognito:groups": "admin" })).toBeNull();
  });
});

describe("emailFromClaims", () => {
  it("reads the id token's email", () => {
    expect(emailFromClaims({ email: "a@example.com" })).toBe("a@example.com");
  });

  it("falls back to username when email is absent", () => {
    // Access tokens have no email — even then there must be a name to display.
    expect(emailFromClaims({ username: "u@example.com" })).toBe("u@example.com");
  });

  it("returns null when neither is present", () => {
    expect(emailFromClaims({})).toBeNull();
    expect(emailFromClaims(null)).toBeNull();
  });
});
