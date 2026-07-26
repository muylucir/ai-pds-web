import { describe, expect, it, vi } from "vitest";
import type { CognitoEnv } from "./cognitoUrls";
import { TokenExchangeError, exchangeCode, refreshTokens } from "./tokenExchange";

const ENV: CognitoEnv = {
  domain: "pool.auth.ap-northeast-2.amazoncognito.com",
  clientId: "client-abc",
  clientSecret: "secret-xyz",
  appUrl: "https://app.example.com",
};

const TOKENS = {
  access_token: "at", id_token: "it", refresh_token: "rt",
  expires_in: 3600, token_type: "Bearer",
};

function okFetch(body: unknown = TOKENS) {
  return vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
}

describe("exchangeCode", () => {
  it("posts the authorization code with PKCE verifier and basic auth", async () => {
    const f = okFetch();
    const tokens = await exchangeCode(ENV, "the-code", "the-verifier", f as never);
    expect(tokens.access_token).toBe("at");

    const [url, init] = f.mock.calls[0];
    expect(url).toBe(`https://${ENV.domain}/oauth2/token`);
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["Content-Type"])
      .toBe("application/x-www-form-urlencoded");
    // confidential 클라이언트는 client_secret_basic으로 인증한다.
    const expected = "Basic " + Buffer.from("client-abc:secret-xyz").toString("base64");
    expect((init?.headers as Record<string, string>).Authorization).toBe(expected);

    const body = new URLSearchParams(init?.body as string);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("the-code");
    expect(body.get("code_verifier")).toBe("the-verifier");
    expect(body.get("redirect_uri")).toBe("https://app.example.com/api/auth/callback");
    // 시크릿은 Authorization 헤더로만 간다 — 본문에 중복해 넣지 않는다.
    expect(body.get("client_secret")).toBeNull();
  });

  it("throws TokenExchangeError on a Cognito error response", async () => {
    const f = vi.fn(async () => new Response(
      JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
    await expect(exchangeCode(ENV, "c", "v", f as never))
      .rejects.toThrow(TokenExchangeError);
  });

  it("throws when the response is missing an access token", async () => {
    const f = okFetch({ id_token: "it", expires_in: 3600 });
    await expect(exchangeCode(ENV, "c", "v", f as never))
      .rejects.toThrow(TokenExchangeError);
  });

  it("throws on a non-JSON response body", async () => {
    const f = vi.fn(async () => new Response("<html>gateway</html>", { status: 200 }));
    await expect(exchangeCode(ENV, "c", "v", f as never))
      .rejects.toThrow(TokenExchangeError);
  });
});

describe("refreshTokens", () => {
  it("posts the refresh_token grant", async () => {
    const f = okFetch({ access_token: "at2", id_token: "it2", expires_in: 3600 });
    const tokens = await refreshTokens(ENV, "the-refresh", f as never);
    expect(tokens.access_token).toBe("at2");

    const body = new URLSearchParams(f.mock.calls[0]?.[1]?.body as string);
    expect(body.get("grant_type")).toBe("refresh_token");
    expect(body.get("refresh_token")).toBe("the-refresh");
    // refresh 그랜트에는 redirect_uri/code_verifier가 없다.
    expect(body.get("redirect_uri")).toBeNull();
    expect(body.get("code_verifier")).toBeNull();
  });

  it("throws when the refresh token has been revoked", async () => {
    const f = vi.fn(async () => new Response(
      JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
    await expect(refreshTokens(ENV, "revoked", f as never))
      .rejects.toThrow(TokenExchangeError);
  });
});
