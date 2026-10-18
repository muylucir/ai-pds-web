// frontend/lib/auth/cognitoUrls.ts
//
// Assembles the Hosted UI (managed login) endpoints. Kept as pure functions so they can be tested
// without a route handler.
//
// ⚠️ All env in this file is server-side only — adding the NEXT_PUBLIC_ prefix would inline it into
// the client bundle and send the client secret to the browser. This module is imported only from
// route handlers and middleware.

export interface CognitoEnv {
  domain: string;
  clientId: string;
  clientSecret: string;
  appUrl: string;
}

// These must match CALLBACK_PATH / LOGOUT_PATH in infra/lib/auth-client-config.ts. Cognito allows
// only exact matches of callback URLs (no wildcards) — if these drift, login fails with
// redirect_mismatch.
const CALLBACK_PATH = "/api/auth/callback";
const LOGOUT_PATH = "/login";
// ⚠️ Must be the same set as OAUTH_SCOPES in infra/lib/auth-client-config.ts.
// If this is broader, Cognito rejects the authorize itself (scopes outside the allowed list);
// if narrower, the issued access token lacks those scopes.
//
// `aws.cognito.signin.user.admin` is for calling Cognito self-service APIs —
// changing one's own password (/me/password → ChangePassword) depends on it. Removing it wipes out
// that screen with "Access Token does not have required scopes".
const SCOPES = "openid email profile aws.cognito.signin.user.admin";

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
    response_type: "code",           // implicit (leaking tokens in the URL fragment) is not used
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
