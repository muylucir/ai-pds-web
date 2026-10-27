import { describe, expect, it } from "vitest";
import { forwardableCookies, isRetryableWithRefresh, withBearer } from "./proxyAuth";

describe("withBearer", () => {
  it("adds the bearer header from the cookie value", () => {
    const out = withBearer(new Headers({ accept: "application/json" }), "tok-1");
    expect(out.get("authorization")).toBe("Bearer tok-1");
    expect(out.get("accept")).toBe("application/json");
  });

  it("strips session cookies so they never reach the backend", () => {
    // The backend does not know the session cookies. Passing them through would needlessly
    // expose the session tokens to one more layer, and they could end up in logs.
    const out = withBearer(
      new Headers({ cookie: "aipds_access=secret; aipds_refresh=alsosecret" }), "tok-1");
    expect(out.get("cookie")).toBeNull();
  });

  it("forwards the prototype access cookie while still dropping session cookies", () => {
    // This combination is the point: within the same Cookie header, one must pass and the rest
    // must be blocked. The prototype preview (/api/proto/*) also goes through this proxy, and
    // the backend decides access by that cookie (_authorized in routes/proto_public.py).
    const out = withBearer(
      new Headers({
        cookie: "aipds_access=secret; aipds_proto_abc123=prototoken; aipds_refresh=alsosecret",
      }),
      "tok-1");
    expect(out.get("cookie")).toBe("aipds_proto_abc123=prototoken");
    // Assert at the string level that no session token leaks out mixed into the value.
    expect(out.get("cookie")).not.toContain("secret");
  });

  it("sends no authorization header when there is no cookie", () => {
    // A local backend with auth disabled responds even without the header.
    const out = withBearer(new Headers(), undefined);
    expect(out.get("authorization")).toBeNull();
  });

  it("replaces a client-supplied authorization header", () => {
    // Do not trust an Authorization sent by the client — the cookie is the source of truth.
    const out = withBearer(new Headers({ authorization: "Bearer forged" }), "tok-1");
    expect(out.get("authorization")).toBe("Bearer tok-1");
  });

  it("drops a client-supplied authorization header when there is no cookie", () => {
    const out = withBearer(new Headers({ authorization: "Bearer forged" }), undefined);
    expect(out.get("authorization")).toBeNull();
  });
});

describe("forwardableCookies", () => {
  it("keeps only aipds_proto_* cookies", () => {
    expect(forwardableCookies("aipds_proto_a=1; aipds_access=2; aipds_proto_b=3"))
      .toBe("aipds_proto_a=1; aipds_proto_b=3");
  });

  it("returns null when nothing is forwardable", () => {
    // Must be null (not an empty string): the caller decides from this value whether to set the
    // header, so an empty string would attach an empty `Cookie: ` header.
    expect(forwardableCookies("aipds_access=1; aipds_id=2; aipds_refresh=3")).toBeNull();
  });

  it("handles a missing header", () => {
    expect(forwardableCookies(null)).toBeNull();
    expect(forwardableCookies(undefined)).toBeNull();
    expect(forwardableCookies("")).toBeNull();
  });

  it("preserves a value containing '=' padding", () => {
    // Tokens are urlsafe-base64-like, so the value may contain "=". The decision looks only at
    // the name, so the value must not be truncated — if it were, the backend's compare_digest
    // would fail and the preview would 404, with the cause buried in cookie parsing and hard to find.
    expect(forwardableCookies("aipds_proto_x=abc==")).toBe("aipds_proto_x=abc==");
  });

  it("does not fall for a session cookie whose VALUE mentions the prefix", () => {
    // The prefix check must apply only to the name. If a string in the value could pass it, an
    // attacker could plant the prefix in the aipds_access value and leak the session token to the backend.
    expect(forwardableCookies("aipds_access=aipds_proto_nope")).toBeNull();
  });

  it("tolerates whitespace between cookie pairs", () => {
    expect(forwardableCookies("aipds_access=1;aipds_proto_a=2;   aipds_proto_b=3"))
      .toBe("aipds_proto_a=2; aipds_proto_b=3");
  });
});

describe("forwardableCookies on a prototype preview", () => {
  it("keeps the prototype's own cookies and the access cookie", () => {
    expect(forwardableCookies("sp_session=hq-1; aipds_proto_ab=t; theme=dark", { preview: true }))
      .toBe("sp_session=hq-1; aipds_proto_ab=t; theme=dark");
  });

  it("drops every app cookie by namespace, including ones not listed anywhere", () => {
    const out = forwardableCookies(
      "aipds_access=a; aipds_id=i; aipds_refresh=r; aipds_pkce=p; aipds_state=s; aipds_next=n; "
      + "aipds_future=x; sp_session=hq-1", { preview: true });
    expect(out).toBe("sp_session=hq-1");
  });

  it("judges by name, not value", () => {
    expect(forwardableCookies("sp=aipds_access", { preview: true })).toBe("sp=aipds_access");
  });

  it("is still the allowlist off the preview path", () => {
    expect(forwardableCookies("sp_session=hq-1; aipds_proto_ab=t")).toBe("aipds_proto_ab=t");
  });
});

describe("isRetryableWithRefresh", () => {
  it("retries a GET on 401 when a refresh token exists", () => {
    expect(isRetryableWithRefresh(401, "GET", true)).toBe(true);
  });

  it("does not retry without a refresh token", () => {
    expect(isRetryableWithRefresh(401, "GET", false)).toBe(false);
  });

  it("does not retry non-401 responses", () => {
    for (const status of [200, 403, 404, 500, 502]) {
      expect(isRetryableWithRefresh(status, "GET", true)).toBe(false);
    }
  });

  it("does not retry methods that carry a streamed body", () => {
    // A request body stream cannot be rewound once consumed — a retry would send an empty
    // body. Only GET/HEAD/DELETE are safe.
    expect(isRetryableWithRefresh(401, "POST", true)).toBe(false);
    expect(isRetryableWithRefresh(401, "PUT", true)).toBe(false);
    expect(isRetryableWithRefresh(401, "GET", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "HEAD", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "DELETE", true)).toBe(true);
  });

  it("is case-insensitive about the method", () => {
    expect(isRetryableWithRefresh(401, "get", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "post", true)).toBe(false);
  });
});
