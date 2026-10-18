// Single source of truth for the app client settings.
//
// Why a separate module: the callback URLs must include the CloudFront domain, which is
// decided at deploy time, so HostingStack updates the client AuthStack created via
// UpdateUserPoolClient. That API has PUT semantics and erases any field not specified, so
// both places must see the same values. A cross-stack import would create a cycle, so this
// is pulled out into a pure constants module.

export const SEED_ADMIN_EMAIL = 'admin@aipds.local';
export const SEED_PM_EMAIL = 'pm@aipds.local';

// The seed password is **not here.** This module knows no password at all.
//
// The seed account's temporary password is received at deploy time by AipdsAuthStack's
// `SeedPassword` parameter (noEcho). As a constant, the value would be committed to the repo
// and would also remain in plaintext in the CloudFormation template and stack events.
//
// And that value is a **temporary** password: the seed account is created under the same
// discipline as invited accounts (FORCE_CHANGE_PASSWORD), and the user sets their own at
// first sign-in. So there is no password this module needs to know in the first place.

export const GROUP_ADMIN = 'admin';
export const GROUP_PM = 'pm';

// Must match the actual paths of the frontend route handler / sign-in screen.
export const CALLBACK_PATH = '/api/auth/callback';
export const LOGOUT_PATH = '/login';

export const LOCAL_APP_URL = 'http://localhost:3000';
// To call the Cognito self-service APIs (`ChangePassword`, `GetUser`, …) with an access
// token, the token must carry this scope. Without it Cognito rejects with "Access Token does
// not have required scopes" — the change-your-own-password screen
// (backend/aipds/routes/account.py) depends entirely on this.
//
// Cost: an access token with this scope can also call other self-service APIs (changing
// attributes, deleting one's own account). Since we do not expose those, the real exposure
// is limited to a stolen token. The alternative (verify the current password with
// AdminInitiateAuth, then AdminSetUserPassword) requires turning on the password auth flow
// we deliberately disabled, and the client secret pulls SECRET_HASH computation into the
// backend — more invasive.
export const COGNITO_ADMIN_SCOPE = 'aws.cognito.signin.user.admin';

export const OAUTH_SCOPES = [
  'openid', 'email', 'profile', COGNITO_ADMIN_SCOPE,
];

// App client name. It is the name shown in the console, so the value itself is trivial, but it
// lives here for the same reason as the other PUT fields — if only AuthStack knows it and
// HostingStack's resend does not, every redeploy silently changes it to Cognito's default name.
export const CLIENT_NAME = 'aipds-web';

// Token validity (in minutes). AuthStack's client definition and HostingStack's
// UpdateUserPoolClient resend must use the same values — with PUT semantics, a mismatch
// silently resets the validity on every redeploy. AuthStack wraps these values in
// cdk.Duration.minutes(); HostingStack sends them as plain integers.
// access/id are 3 hours because of **a single prototype build**. A build usually finishes
// within an hour, and the previous value (60 minutes) sat exactly on that boundary, leaving zero margin.
//
// The primary cause of expiry was not the lifetime but the lack of any chance to refresh: token
// refresh only fires when the /api proxy receives a backend 401 (app/api/[...path]/route.ts),
// and an SSE connection that lives for tens of minutes after `GET /events` opens with 200 can
// never produce that 401 again. So while a build ran there was not a single chance to refresh.
// That side was fixed with periodic refresh (frontend/lib/auth/keepSessionAlive.ts + /api/auth/refresh).
//
// This value is the **second line of defense**. Even if periodic refresh fails several times
// in a row (network drop, transient Cognito error), the user does not hit expiry as long as a
// build finishes within one token lifetime. Both layers are needed because they guard against
// different failures: refresh covers sessions longer than 3 hours, and this lifetime covers
// failure of the refresh itself.
//
// Why not extend to 24 hours (the Cognito maximum): the validity window of a stolen token grows
// by the same amount. 3 hours covers a build while keeping that window practically short.
// infra/test/auth-client-config.assert.ts guards this rationale as an invariant.
export const ACCESS_TOKEN_VALIDITY_MINUTES = 180; // 3 hours
export const ID_TOKEN_VALIDITY_MINUTES = 180; // 3 hours — must equal access
export const REFRESH_TOKEN_VALIDITY_MINUTES = 60 * 24 * 30; // 30 days

// The reason AuthStack's client explicitly enables only ALLOW_REFRESH_TOKEN_AUTH lies in
// the authFlows setting in aipds-auth-stack.ts (userSrp/userPassword both false) — with
// no other flag enabled and refreshTokenRotationGracePeriod unspecified, CDK automatically
// adds just this one flow. The /api proxy's 401 refresh path depends on this flow, so if it
// is missing from the UpdateUserPoolClient resend, refresh silently breaks on every
// redeploy.
export const EXPLICIT_AUTH_FLOWS = ['ALLOW_REFRESH_TOKEN_AUTH'];

function join(appUrl: string, path: string): string {
  return `${appUrl.replace(/\/$/, '')}${path}`;
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

export function callbackUrls(appUrls: string[]): string[] {
  return unique(appUrls.map((u) => join(u, CALLBACK_PATH)));
}

export function logoutUrls(appUrls: string[]): string[] {
  return unique(appUrls.map((u) => join(u, LOGOUT_PATH)));
}

/**
 * Email → Cognito Username (local part).
 *
 * This pool has AliasAttributes=[email]. With that setting Cognito **rejects an
 * email-format Username** — "Username cannot be of email format, since user pool
 * is configured for email alias". The email is reserved as an alias, so it would
 * collide with the username. Observed: not knowing this rule, seed account creation caused a stack rollback.
 *
 * So only the part before '@' is used as the Username. Either way the user signs in
 * with their email (the email alias does that). The reason for not using UUID
 * auto-generation (UsernameAttributes) is the same as the README's "username == email"
 * section — the value cannot be known across redeploys, so seeding becomes nondeterministic.
 *
 * ⚠️ **Must follow the same rule as username_for_email in backend/aipds/auth/cognito.py.**
 * If they diverge, the Username rules for seed and invited accounts split, and on redeploy
 * the seed cannot find the existing user and creates a duplicate account.
 *
 * ⚠️ Two accounts with the same local part but different domains (kim@example.com /
 * kim@example.org) collide on the same Username — a trade-off accepted at workshop scale (single domain).
 */
export function usernameForEmail(email: string): string {
  const local = email.trim().toLowerCase().split('@')[0];
  // The characters allowed in a Cognito Username are limited (e.g., the '+' of a tagged address is not allowed).
  const safe = local.replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  if (!safe) {
    throw new Error(`cannot derive a Cognito username from email: ${email}`);
  }
  return safe;
}
