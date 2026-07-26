// frontend/lib/auth/tokenExchange.ts
//
// Cognito /oauth2/token 호출. route 파일이 아니라 여기 있는 이유:
//   - Next route 파일은 HTTP 메서드 export만 허용한다(lib/api/rewriteLocation.ts가
//     같은 이유로 분리돼 있다)
//   - /api 프록시가 refreshTokens를 재사용한다
//
// 이 코드는 서버사이드에서만 돈다 — client secret이 여기 있기 때문이다.
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
  // confidential 클라이언트는 client_secret_basic으로 인증한다 — 시크릿을
  // 본문에 중복해 넣지 않는다.
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
    // Cognito는 교환 시에도 redirect_uri가 authorize 때와 같은지 확인한다.
    redirect_uri: callbackUrl(env),
  }), fetchImpl);
}

export async function refreshTokens(env: CognitoEnv, refreshToken: string,
                                    fetchImpl: typeof fetch = fetch): Promise<TokenSet> {
  // refresh 그랜트는 새 refresh_token을 반환하지 않는다(기존 것이 계속 유효).
  return post(env, new URLSearchParams({
    grant_type: "refresh_token",
    client_id: env.clientId,
    refresh_token: refreshToken,
  }), fetchImpl);
}
