import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  usernameForEmail,
  CALLBACK_PATH, COGNITO_ADMIN_SCOPE, GROUP_ADMIN, GROUP_PM, LOCAL_APP_URL,
  LOGOUT_PATH, OAUTH_SCOPES,
  SEED_ADMIN_EMAIL, SEED_PM_EMAIL,
  ACCESS_TOKEN_VALIDITY_MINUTES, ID_TOKEN_VALIDITY_MINUTES,
  REFRESH_TOKEN_VALIDITY_MINUTES,
  callbackUrls, logoutUrls,
} from '../lib/auth-client-config';

// --- Token validity: must comfortably cover one full prototype build.
//
// Observed defect: the session expired mid-build and the user was logged out. The
// primary cause was that refresh only reacted to a backend 401, and an open SSE
// connection cannot produce a 401; that was fixed with periodic refresh
// (frontend/lib/auth/keepSessionAlive.ts + /api/auth/refresh). These constants are
// the **second line of defense**: even if refresh fails several times in a row
// (network outage, transient Cognito error), the user never hits expiry as long as
// one build finishes within a single token lifetime.
//
// A build usually takes under an hour. A 60-minute access token lands exactly on that
// boundary with zero margin, so we require at least 2x. Cognito caps access/id at 24 hours.
assert.ok(ACCESS_TOKEN_VALIDITY_MINUTES >= 120,
  'access token must outlast a full prototype build (~1h) with margin — 60m leaves none');
assert.ok(ACCESS_TOKEN_VALIDITY_MINUTES <= 24 * 60,
  'Cognito caps access-token validity at 24 hours');
// The id token keeps the same lifetime as the access token. If the shorter one expires
// first, /api/auth/me loses email and role and the header looks empty, yet the session
// is still alive, which makes the cause hard to find.
assert.strictEqual(ID_TOKEN_VALIDITY_MINUTES, ACCESS_TOKEN_VALIDITY_MINUTES,
  'id token must expire with the access token — a shorter one empties the header while the session lives');
// The refresh window is the entire period in which refresh is possible. Once it ends, periodic refresh is powerless too.
assert.strictEqual(REFRESH_TOKEN_VALIDITY_MINUTES, 60 * 24 * 30);
console.log('OK  auth-client-config: token validity outlasts a prototype build');

// --- This module knows no password whatsoever.
//
// The seed accounts' temporary password comes from a deploy-time `CfnParameter`
// (noEcho) (AipdsAuthStack). As a constant, the value would be committed to the repo
// and would also remain in plaintext in the CloudFormation template and stack events.
//
// The absence of a constant cannot be verified via import — importing a missing
// export breaks compilation and this file would not even run. So we inspect the
// source text directly. We block a name **pattern** rather than one specific name
// because what this invariant protects is not "that constant does not exist" but
// "passwords do not live here".
{
  const source = fs.readFileSync(
    path.join(__dirname, '../lib/auth-client-config.ts'), 'utf8');
  const declaration = /^[^\n]*\b[A-Za-z_]*PASSWORD[A-Za-z_]*\s*=/mi;
  const hit = source.match(declaration);
  assert.ok(
    hit === null,
    'auth-client-config.ts must not declare a password — the value comes from a '
    + 'deploy-time CfnParameter(noEcho) so it never lands in the template or stack '
    + `events. Found: ${hit?.[0].trim()}`,
  );
}

assert.strictEqual(SEED_ADMIN_EMAIL, 'admin@aipds.local');
assert.strictEqual(SEED_PM_EMAIL, 'pm@aipds.local');

// Task 3 creates the Cognito groups from these values, and Task 4's token verification
// matches the same strings against the cognito:groups claim — a typo would pass all the
// way through deployment and surface only as a runtime authorization error, so we
// assert the constants themselves.
assert.strictEqual(GROUP_ADMIN, 'admin');
assert.strictEqual(GROUP_PM, 'pm');

// Callback URLs do not allow wildcards and accept only exact matches — the path must
// be exactly the same as the frontend route handler path.
assert.strictEqual(CALLBACK_PATH, '/api/auth/callback');
assert.strictEqual(LOGOUT_PATH, '/login');

// Build one callback per app URL (local + CloudFront).
assert.deepStrictEqual(
  callbackUrls([LOCAL_APP_URL, 'https://d123.cloudfront.net']),
  ['http://localhost:3000/api/auth/callback',
   'https://d123.cloudfront.net/api/auth/callback'],
);
assert.deepStrictEqual(
  logoutUrls(['https://d123.cloudfront.net']),
  ['https://d123.cloudfront.net/login'],
);

// An app URL with a trailing slash must not produce a double slash.
assert.deepStrictEqual(
  callbackUrls(['https://d123.cloudfront.net/']),
  ['https://d123.cloudfront.net/api/auth/callback'],
);

// Deduplicate URLs — Cognito rejects duplicate callback URLs.
assert.deepStrictEqual(
  callbackUrls([LOCAL_APP_URL, LOCAL_APP_URL]),
  ['http://localhost:3000/api/auth/callback'],
);

// Without `aws.cognito.signin.user.admin`, the access token cannot call the Cognito
// self-service APIs — `ChangePassword` is rejected with "Access Token does not have
// required scopes", and the change-your-own-password screen fails entirely
// (backend/aipds/routes/account.py). The list must have a single source here so that
// the AuthStack client definition and HostingStack's UpdateUserPoolClient resend (PUT
// semantics) see the same value — if only one side omits it, the scope silently
// disappears on every redeploy.
assert.deepStrictEqual(
  OAUTH_SCOPES,
  ['openid', 'email', 'profile', 'aws.cognito.signin.user.admin'],
);
assert.strictEqual(COGNITO_ADMIN_SCOPE, 'aws.cognito.signin.user.admin');
assert.ok(
  OAUTH_SCOPES.includes(COGNITO_ADMIN_SCOPE),
  'the self-service scope constant must be part of the requested scope set',
);
console.log('OK  auth-client-config: seed constants + group constants + callback/logout URL derivation');

// --- usernameForEmail: Cognito rejects an email-formatted Username in an email-alias
// pool ("Username cannot be of email format, since user pool is configured
// for email alias"). Observed: not knowing this rule, seed account creation caused a
// stack rollback. This must be the same rule as username_for_email in
// backend/aipds/auth/cognito.py — if they diverge, the Username rules for invited
// accounts and seed accounts split apart.
assert.strictEqual(usernameForEmail('admin@aipds.local'), 'admin');
assert.strictEqual(usernameForEmail('pm@aipds.local'), 'pm');
// The values when actually applied to the seed constants — these are the deployed Usernames.
assert.strictEqual(usernameForEmail(SEED_ADMIN_EMAIL), 'admin');
assert.strictEqual(usernameForEmail(SEED_PM_EMAIL), 'pm');
// Invariant: the result must never contain '@' (the only condition Cognito rejects).
for (const email of [SEED_ADMIN_EMAIL, SEED_PM_EMAIL, 'a+tag@example.com', 'Mixed@EXAMPLE.COM']) {
  assert.ok(!usernameForEmail(email).includes('@'),
    `usernameForEmail(${email}) must not be email-shaped`);
}
// Case and whitespace normalization; substitution of characters Cognito does not accept.
assert.strictEqual(usernameForEmail('  Mixed@EXAMPLE.COM  '), 'mixed');
assert.strictEqual(usernameForEmail('a+tag@example.com'), 'a-tag');
console.log('OK  usernameForEmail: local-part rule, no email-shaped username, normalization');
