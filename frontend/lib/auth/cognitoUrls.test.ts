import { describe, expect, it } from "vitest";
import {
  authorizeUrl, callbackUrl, logoutUrl, tokenEndpoint, type CognitoEnv,
} from "./cognitoUrls";

const ENV: CognitoEnv = {
  domain: "aipds-123-ap-northeast-2.auth.ap-northeast-2.amazoncognito.com",
  clientId: "client-abc",
  clientSecret: "secret-xyz",
  appUrl: "https://d123.cloudfront.net",
};

describe("authorizeUrl", () => {
  it("builds a PKCE authorization-code request", () => {
    const url = new URL(authorizeUrl(ENV, "challenge-123", "state-456"));
    expect(url.origin).toBe(`https://${ENV.domain}`);
    expect(url.pathname).toBe("/oauth2/authorize");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("client_id")).toBe("client-abc");
    expect(url.searchParams.get("code_challenge")).toBe("challenge-123");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("state")).toBe("state-456");
    // Without aws.cognito.signin.user.admin, the issued access token cannot call Cognito's
    // self-service APIs — changing one's own password (/me/password) fails across the board with
    // "Access Token does not have required scopes". The list the app client
    // allows is OAUTH_SCOPES in infra/lib/auth-client-config.ts, and if the list requested here
    // is broader than that, Cognito rejects the authorize request itself.
    expect(url.searchParams.get("scope"))
      .toBe("openid email profile aws.cognito.signin.user.admin");
    expect(url.searchParams.get("redirect_uri"))
      .toBe("https://d123.cloudfront.net/api/auth/callback");
  });

  it("never requests an implicit-flow token", () => {
    // response_type=token leaks the token in the URL fragment.
    const url = new URL(authorizeUrl(ENV, "c", "s"));
    expect(url.searchParams.get("response_type")).not.toContain("token");
  });
});

describe("callbackUrl", () => {
  it("matches the Cognito-registered path exactly", () => {
    // Cognito only allows exact matches on callback URLs (no wildcards) — this string must
    // match CALLBACK_PATH in infra/lib/auth-client-config.ts.
    expect(callbackUrl(ENV)).toBe("https://d123.cloudfront.net/api/auth/callback");
  });

  it("does not double the slash when appUrl has a trailing one", () => {
    expect(callbackUrl({ ...ENV, appUrl: "https://d123.cloudfront.net/" }))
      .toBe("https://d123.cloudfront.net/api/auth/callback");
  });
});

describe("tokenEndpoint", () => {
  it("points at the pool's oauth2/token", () => {
    expect(tokenEndpoint(ENV)).toBe(`https://${ENV.domain}/oauth2/token`);
  });
});

describe("logoutUrl", () => {
  it("returns the user to /login", () => {
    const url = new URL(logoutUrl(ENV));
    expect(url.pathname).toBe("/logout");
    expect(url.searchParams.get("client_id")).toBe("client-abc");
    expect(url.searchParams.get("logout_uri"))
      .toBe("https://d123.cloudfront.net/login");
  });
});
