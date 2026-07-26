// frontend/lib/auth/cognitoUrls.ts
//
// Hosted UI(managed login) 엔드포인트 조립. 순수 함수로 두어 route handler 없이
// 테스트할 수 있게 한다.
//
// ⚠️ 이 파일의 env는 모두 서버사이드 전용이다 — NEXT_PUBLIC_ 접두어를 붙이면
// 클라이언트 번들에 인라인되어 client secret이 브라우저로 나간다. 이 모듈은
// route handler와 middleware에서만 import한다.

export interface CognitoEnv {
  domain: string;
  clientId: string;
  clientSecret: string;
  appUrl: string;
}

// infra/lib/auth-client-config.ts의 CALLBACK_PATH / LOGOUT_PATH와 반드시 같아야
// 한다. Cognito는 콜백 URL의 전수 일치만 허용한다(와일드카드 불가) — 여기가
// 어긋나면 로그인이 redirect_mismatch로 실패한다.
const CALLBACK_PATH = "/api/auth/callback";
const LOGOUT_PATH = "/login";
const SCOPES = "openid email profile";

export function cognitoEnv(): CognitoEnv {
  return {
    domain: process.env.COGNITO_HOSTED_UI_DOMAIN ?? "",
    clientId: process.env.COGNITO_CLIENT_ID ?? "",
    clientSecret: process.env.COGNITO_CLIENT_SECRET ?? "",
    appUrl: process.env.APP_BASE_URL ?? "http://localhost:3000",
  };
}

function origin(appUrl: string): string {
  return appUrl.replace(/\/$/, "");
}

export function callbackUrl(env: CognitoEnv): string {
  return `${origin(env.appUrl)}${CALLBACK_PATH}`;
}

export function authorizeUrl(env: CognitoEnv, challenge: string,
                             state: string): string {
  const params = new URLSearchParams({
    response_type: "code",           // implicit(토큰을 URL 프래그먼트로 흘림)은 쓰지 않는다
    client_id: env.clientId,
    redirect_uri: callbackUrl(env),
    scope: SCOPES,
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `https://${env.domain}/oauth2/authorize?${params}`;
}

export function tokenEndpoint(env: CognitoEnv): string {
  return `https://${env.domain}/oauth2/token`;
}

export function logoutUrl(env: CognitoEnv): string {
  const params = new URLSearchParams({
    client_id: env.clientId,
    logout_uri: `${origin(env.appUrl)}${LOGOUT_PATH}`,
  });
  return `https://${env.domain}/logout?${params}`;
}
