import { describe, expect, it } from "vitest";
import {
  decodeJwtPayload, emailFromClaims, isExpired, roleFromClaims,
} from "./claims";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256", kid: "k" })}.${b64(payload)}.fake-signature`;
}

describe("decodeJwtPayload", () => {
  it("reads the payload without verifying anything", () => {
    const c = decodeJwtPayload(fakeJwt({ sub: "s-1", email: "a@b.io" }));
    expect(c).toMatchObject({ sub: "s-1", email: "a@b.io" });
  });

  it("returns null for garbage instead of throwing", () => {
    // 미들웨어가 이걸 부른다 — 예외가 나면 모든 페이지가 500이 된다.
    expect(decodeJwtPayload("not-a-jwt")).toBeNull();
    expect(decodeJwtPayload("")).toBeNull();
    expect(decodeJwtPayload("a.b")).toBeNull();
    expect(decodeJwtPayload("a.!!!.c")).toBeNull();
  });

  it("handles base64url payloads that need padding", () => {
    const c = decodeJwtPayload(fakeJwt({ a: "x".repeat(5) }));
    // 값 자체를 확인한다 — not.toBeNull()만으로는 잘리거나 잘못 디코딩된
    // 결과도 통과해버린다.
    expect(c).toMatchObject({ a: "xxxxx" });
  });

  it("returns null for a bare JSON array payload", () => {
    // 배열도 typeof는 "object"다 — Claims는 객체 계약이므로 배열은 거부한다.
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
    // 백엔드 verifier와 같은 우선순위여야 한다 — 어긋나면 화면과 권한이 불일치한다.
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
    expect(emailFromClaims({ email: "a@b.io" })).toBe("a@b.io");
  });

  it("falls back to username when email is absent", () => {
    // access 토큰에는 email이 없다 — 그 경우에도 표시할 이름은 있어야 한다.
    expect(emailFromClaims({ username: "u@b.io" })).toBe("u@b.io");
  });

  it("returns null when neither is present", () => {
    expect(emailFromClaims({})).toBeNull();
    expect(emailFromClaims(null)).toBeNull();
  });
});

describe("isExpired", () => {
  it("compares exp against the given time", () => {
    expect(isExpired({ exp: 1000 }, 999)).toBe(false);
    expect(isExpired({ exp: 1000 }, 1001)).toBe(true);
  });

  it("treats a missing or malformed exp as expired", () => {
    // fail-closed: exp를 못 읽으면 만료로 본다(리프레시를 유발할 뿐 위험하지 않다).
    expect(isExpired({}, 0)).toBe(true);
    expect(isExpired({ exp: "soon" }, 0)).toBe(true);
    expect(isExpired(null, 0)).toBe(true);
  });
});
