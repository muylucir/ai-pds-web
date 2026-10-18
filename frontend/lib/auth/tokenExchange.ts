// frontend/lib/auth/tokenExchange.ts
//
// Calls Cognito /oauth2/token. Why it lives here rather than in a route file:
//   - Next route files allow only HTTP method exports (lib/api/rewriteLocation.ts is
//     split out for the same reason)
//   - the /api proxy reuses refreshTokens
//
// This code runs only on the server side — because the client secret lives here.
import { callbackUrl, tokenEndpoint, type CognitoEnv } from "./cognitoUrls";

export interface TokenSet {
  access_token: string;
  id_token: string;
  refresh_token?: string;
  expires_in: number;
}

export class TokenExchangeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TokenExchangeError";
  }
}

async function post(env: CognitoEnv, body: URLSearchParams,
                    fetchImpl: typeof fetch): Promise<TokenSet> {
  // A confidential client authenticates with client_secret_basic — the secret is not
  // duplicated into the body.
  const basic = Buffer.from(`${env.clientId}:${env.clientSecret}`).toString("base64");
  const res = await fetchImpl(tokenEndpoint(env), {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: `Basic ${basic}`,
    },
    body: body.toString(),
    cache: "no-store",
  });

  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    throw new TokenExchangeError(
      `token endpoint returned a non-JSON body (status ${res.status})`);
  }
  if (!res.ok) {
    const error = (payload as { error?: string })?.error ?? "unknown_error";
    throw new TokenExchangeError(`token endpoint rejected the request: ${error}`);
  }
  const tokens = payload as Partial<TokenSet>;
  if (!tokens.access_token || !tokens.id_token) {
    throw new TokenExchangeError("token response is missing access/id token");
  }
  return {
    access_token: tokens.access_token,
    id_token: tokens.id_token,
    refresh_token: tokens.refresh_token,
    expires_in: tokens.expires_in ?? 3600,
  };
}

export async function exchangeCode(env: CognitoEnv, code: string,
                                   verifier: string,
                                   fetchImpl: typeof fetch = fetch): Promise<TokenSet> {
  return post(env, new URLSearchParams({
    grant_type: "authorization_code",
    client_id: env.clientId,
    code,
    code_verifier: verifier,
    // Cognito checks during the exchange too that redirect_uri matches the one used at authorize.
    redirect_uri: callbackUrl(env),
  }), fetchImpl);
}

export async function refreshTokens(env: CognitoEnv, refreshToken: string,
                                    fetchImpl: typeof fetch = fetch): Promise<TokenSet> {
  // The refresh grant does not return a new refresh_token (the existing one stays valid).
  return post(env, new URLSearchParams({
    grant_type: "refresh_token",
    client_id: env.clientId,
    refresh_token: refreshToken,
  }), fetchImpl);
}
