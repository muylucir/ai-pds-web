import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import {
  COGNITO_ADMIN_SCOPE, GROUP_ADMIN, GROUP_PM, SEED_ADMIN_EMAIL, SEED_PM_EMAIL,
  usernameForEmail,
} from '../lib/auth-client-config';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

const app = new cdk.App();
const stack = new AipdsAuthStack(app, 'Auth', { env: ENV });
const t = Template.fromStack(stack);

// --- Block self-signup: the substance of this requirement is this one field. ---
t.hasResourceProperties('AWS::Cognito::UserPool', {
  AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
});

// --- The caller sets the username (AliasAttributes); Cognito does not generate it. ---
// If UsernameAttributes is set, Cognito makes the username a UUID, and seeding and
// admin API calls become nondeterministic.
t.hasResourceProperties('AWS::Cognito::UserPool', {
  AliasAttributes: ['email'],
});
const pools = t.findResources('AWS::Cognito::UserPool');
const poolProps = Object.values(pools)[0].Properties;
assert.ok(
  poolProps.UsernameAttributes === undefined,
  'UsernameAttributes must be absent — it would make Cognito auto-generate usernames',
);

// --- Password policy: the seed password must pass. ---
t.hasResourceProperties('AWS::Cognito::UserPool', {
  Policies: {
    PasswordPolicy: {
      MinimumLength: 8,
      RequireLowercase: true,
      RequireUppercase: true,
      RequireNumbers: true,
      RequireSymbols: true,
    },
  },
});

// --- Temporary password validity: must survive the gap between deploy and workshop. ---
//
// Seed accounts are now created with a temporary password (FORCE_CHANGE_PASSWORD), so this window
// is exactly "the period during which the deployed accounts can log in". If unset the default is 7 days,
// and if the gap between deploy and workshop is longer than that, the seed accounts cannot log in and the symptom
// only looks like "the password is right but it will not let me in".
t.hasResourceProperties('AWS::Cognito::UserPool', {
  Policies: { PasswordPolicy: { TemporaryPasswordValidityDays: 30 } },
});

// --- Account recovery is admin-only (no email is sent, so no self-service reset). ---
t.hasResourceProperties('AWS::Cognito::UserPool', {
  AccountRecoverySetting: {
    RecoveryMechanisms: [{ Name: 'admin_only', Priority: 1 }],
  },
});

// --- 2 groups + precedence. ---
t.resourceCountIs('AWS::Cognito::UserPoolGroup', 2);
t.hasResourceProperties('AWS::Cognito::UserPoolGroup', {
  GroupName: 'admin', Precedence: 0,
});
t.hasResourceProperties('AWS::Cognito::UserPoolGroup', {
  GroupName: 'pm', Precedence: 10,
});

// --- Hosted UI v2 (managed login). With v1, the old UI shows instead of the branding designer. ---
t.hasResourceProperties('AWS::Cognito::UserPoolDomain', {
  ManagedLoginVersion: 2,
  Domain: 'aipds-123456789012-ap-northeast-2',
});
// v2 renders correctly only with a branding style record.
t.hasResourceProperties('AWS::Cognito::ManagedLoginBranding', {
  UseCognitoProvidedValues: true,
});
// ClientId is optional in the CDK type but required by the Cognito API — branding styles are
// attached per app client. Leave it out and synth/unit tests pass but a real deploy
// dies with "Value null at 'clientId' failed to satisfy constraint" (observed:
// AipdsAuthStack ROLLBACK). Also checks that it points at the actual client.
const brandings = t.findResources('AWS::Cognito::ManagedLoginBranding');
const brandingProps = Object.values(brandings)[0].Properties;
assert.ok(
  brandingProps.ClientId !== undefined,
  'ManagedLoginBranding.ClientId must be set — Cognito rejects null even though CDK types it optional',
);
const clientLogicalIds = Object.keys(t.findResources('AWS::Cognito::UserPoolClient'));
assert.deepStrictEqual(
  brandingProps.ClientId,
  { Ref: clientLogicalIds[0] },
  'ManagedLoginBranding.ClientId must reference the web app client',
);

// --- App client: authorization code grant only, with a secret. ---
t.hasResourceProperties('AWS::Cognito::UserPoolClient', {
  GenerateSecret: true,
  AllowedOAuthFlows: ['code'],
  AllowedOAuthFlowsUserPoolClient: true,
  // Without the self-service scope, ChangePassword cannot be called with the access token —
  // changing one's own password fails across the board with "Access Token does not have required scopes".
  AllowedOAuthScopes: Match.arrayWith([
    'openid', 'email', 'profile', COGNITO_ADMIN_SCOPE]),
  CallbackURLs: ['http://localhost:3000/api/auth/callback'],
  LogoutURLs: ['http://localhost:3000/login'],
});
// The implicit grant leaks tokens in the URL fragment, so it is never allowed.
const clients = t.findResources('AWS::Cognito::UserPoolClient');
const clientProps = Object.values(clients)[0].Properties;
assert.ok(
  !clientProps.AllowedOAuthFlows.includes('implicit'),
  'implicit grant must not be allowed',
);

// --- Seeding: 2 accounts × 3 steps = 6 custom resources. ---
// The Create/Update field of each Custom::AWS that AwsCustomResource creates is JSON describing
// the SDK call, assembled with Fn::Join (only the UserPoolId part is { Ref: ... }).
// Joining the strings and parsing them yields the actual service/action/parameters
// — that parsing is what lets us verify "which resource pairs with which account and group".
// If everything were JSON.stringify'd into one string and searched with
// includes(), it would pass even if admin's and pm's groups were swapped, Permanent were
// missing, or SUPPRESS were on only one side — so each resource is
// parsed and checked.
function parseSdkCall(field: any): { action: string; parameters: any } | undefined {
  if (!field) return undefined;
  const parts = field['Fn::Join'][1];
  const joined = parts.map((p: any) => (typeof p === 'string' ? p : '')).join('');
  return JSON.parse(joined);
}

// parseSdkCall erases non-string fragments as '', so values that came in as references show up as
// empty strings in the parsed parameters. The core of this change is that the password is
// **a parameter reference, not a literal**, so those fragments are examined separately.
function refsIn(field: any): string[] {
  if (!field) return [];
  return (field['Fn::Join'][1] as any[])
    .filter((p) => p && typeof p === 'object' && typeof p.Ref === 'string')
    .map((p) => p.Ref);
}

const customResources = t.findResources('Custom::AWS');
const resourceList = Object.values(customResources);
assert.strictEqual(
  resourceList.length, 6,
  `expected 6 seed custom resources, got ${resourceList.length}`,
);

const calls = resourceList.map((r: any) => ({
  create: parseSdkCall(r.Properties.Create),
  update: parseSdkCall(r.Properties.Update),
  createRefs: refsIn(r.Properties.Create),
  rawUpdate: r.Properties.Update,
}));

// --- The seed password is a deploy-time parameter (not a source constant) ---
//
// As a constant, the value would be committed to the repo and left in plaintext in the CloudFormation template
// and stack events too. `noEcho` cuts off that exposure: only a `Ref` remains in the template, and the value
// does not appear in GetTemplate or stack events.
//
// `allowedPattern` serves a separate purpose — a value that does not satisfy the Cognito policy is
// rejected by `AdminSetUserPassword` with `InvalidPasswordException`, and **the whole stack
// rolls back**. It pulls that failure, which happens minutes into the deploy, forward to the parameter validation stage.
const SEED_PASSWORD_PARAM = 'SeedPassword';
{
  const params = t.findParameters('*');
  const param = params[SEED_PASSWORD_PARAM];
  assert.ok(
    param,
    `parameter ${SEED_PASSWORD_PARAM} must exist — the seed password comes from the `
    + 'deploy command, not from source. Found: ' + Object.keys(params).join(', '),
  );
  assert.strictEqual(
    param.NoEcho, true,
    'the seed password parameter must be NoEcho — otherwise the value returns in '
    + 'GetTemplate/DescribeStacks and the exposure this change removes comes right back',
  );
  assert.ok(
    param.Default === undefined,
    'the seed password must have NO default — a default is a hardcoded password with '
    + 'extra steps, and it would deploy silently',
  );
  assert.ok(
    typeof param.AllowedPattern === 'string' && param.AllowedPattern.length > 0,
    'the seed password parameter must carry an AllowedPattern so a policy-violating '
    + 'value fails at parameter validation instead of rolling the stack back',
  );
  // Whether the pattern actually filters by the pool policy — check behavior, not wording.
  const pattern = new RegExp(`^(?:${param.AllowedPattern})$`);
  for (const bad of ['Ab1!de', 'nouppercase1!', 'NOLOWERCASE1!', 'NoDigits!!',
                     'NoSymbols123']) {
    assert.ok(
      !pattern.test(bad),
      `AllowedPattern must reject '${bad}' — Cognito's policy would, and there the `
      + 'rejection costs a stack rollback',
    );
  }
  assert.ok(
    pattern.test('Workshop2026!x'),
    'AllowedPattern must accept a value that satisfies the pool policy',
  );
}

// Having a parameter alone does not close the exposure — if something sends a literal through as is,
// the value ends up in the template again. So assert that the seed call's `Password` contains
// **not even one character** as a literal: parseSdkCall erases non-string fragments as '',
// so if the whole value comes from references, the parse result is an empty string.
for (const c of calls) {
  if (c.create?.action !== 'adminSetUserPassword') continue;
  assert.strictEqual(
    c.create.parameters.Password, '',
    'the seeded password must come entirely from the NoEcho parameter — any literal '
    + `character here lands in the template in plaintext (got '${c.create.parameters.Password}')`,
  );
}

function assertSeeding(email: string, group: string) {
  // Username is the local part, not the email — Cognito rejects an email-format Username in an
  // email-alias pool (observed: stack rollback). All three calls must use the same value so
  // the password and group apply to the account just created.
  const username = usernameForEmail(email);

  // 1) adminCreateUser: with this account's Username, invitation email suppressed, email verified.
  const createCalls = calls
    .map((c) => c.create)
    .filter((c) => c?.action === 'adminCreateUser' && c?.parameters.Username === username);
  assert.strictEqual(
    createCalls.length, 1,
    `expected exactly 1 adminCreateUser for ${email}, got ${createCalls.length}`,
  );
  assert.strictEqual(
    createCalls[0]!.parameters.MessageAction, 'SUPPRESS',
    `${email}: invite email must be suppressed`,
  );
  const emailVerified = createCalls[0]!.parameters.UserAttributes.find(
    (a: any) => a.Name === 'email_verified',
  );
  assert.strictEqual(
    emailVerified?.Value, 'true',
    `${email}: email_verified must be true`,
  );
  // Username is not the email, but the email attribute must be the original email — that is
  // the target of alias sign-in. Changing it to the local part would make login impossible.
  const emailAttr = createCalls[0]!.parameters.UserAttributes.find(
    (a: any) => a.Name === 'email',
  );
  assert.strictEqual(
    emailAttr?.Value, email,
    `${email}: the email attribute must keep the full address (alias sign-in target)`,
  );

  // 2) adminSetUserPassword: with this account's Username, plants a **temporary** password.
  //
  // `Permanent: false` leaves the account in FORCE_CHANGE_PASSWORD, and the Hosted UI makes the user set a new
  // password at first login — the same discipline as invited accounts
  // (set_temp_password in backend/aipds/auth/cognito.py). Reverting to `true` would make the deployed
  // value that account's permanent password, letting anyone in with the password written in the docs.
  const passwordResources = calls.filter(
    (c) => c.create?.action === 'adminSetUserPassword'
      && c.create?.parameters.Username === username);
  assert.strictEqual(
    passwordResources.length, 1,
    `expected exactly 1 adminSetUserPassword for ${email}, got ${passwordResources.length}`,
  );
  const passwordResource = passwordResources[0]!;
  assert.strictEqual(
    passwordResource.create!.parameters.Permanent, false,
    `${email}: the seeded password must be TEMPORARY — Permanent:true makes the `
    + 'deploy-time value that account\'s standing password',
  );
  // The value must be a parameter reference. A literal would remain in plaintext in the template.
  assert.ok(
    passwordResource.createRefs.includes(SEED_PASSWORD_PARAM),
    `${email}: the password must be a Ref to ${SEED_PASSWORD_PARAM}, not a literal. `
    + `Refs found: ${passwordResource.createRefs.join(', ')}`,
  );
  // A redeploy must not revert the password. With `onUpdate`, the next `cdk deploy` would overwrite the
  // password the user set at first login with the deploy-time value, and the
  // account would go back to FORCE_CHANGE_PASSWORD.
  assert.strictEqual(
    passwordResource.rawUpdate, undefined,
    `${email}: the password custom resource must have no onUpdate — a redeploy would `
    + 'otherwise overwrite the password the user chose at first login',
  );

  // 3) adminAddUserToGroup: this account's Username must pair with exactly this group
  // — it is the only source of the role, so swapping admin/pm is a permissions incident as is.
  const groupCalls = calls
    .map((c) => c.create)
    .filter((c) => c?.action === 'adminAddUserToGroup' && c?.parameters.Username === username);
  assert.strictEqual(
    groupCalls.length, 1,
    `expected exactly 1 adminAddUserToGroup for ${email}, got ${groupCalls.length}`,
  );
  assert.strictEqual(
    groupCalls[0]!.parameters.GroupName, group,
    `${email}: must be added to group '${group}', got '${groupCalls[0]!.parameters.GroupName}'`,
  );
}

assertSeeding(SEED_ADMIN_EMAIL, GROUP_ADMIN);
assertSeeding(SEED_PM_EMAIL, GROUP_PM);

// Global invariant: no Admin* call may carry an email-format Username. That is the only condition
// Cognito rejects, so assert this invariant directly rather than the rule (local part)
// — even if the rule changes later, deploy failures stay blocked.
for (const c of calls) {
  for (const call of [c.create, c.update]) {
    const u = call?.parameters?.Username;
    if (typeof u === 'string') {
      assert.ok(
        !u.includes('@'),
        `Cognito rejects email-shaped usernames in an email-alias pool; got '${u}' in ${call?.action}`,
      );
    }
  }
}

// --- Outputs: used as backend/frontend env. ---
const outputs = t.findOutputs('*');
for (const key of ['UserPoolId', 'UserPoolClientId', 'HostedUiDomain']) {
  assert.ok(outputs[key], `output ${key} must exist`);
}
// The client secret is not output — EC2 looks it up at boot.
assert.ok(!outputs.ClientSecret, 'client secret must NOT be a CfnOutput');

// --- References the stack exposes (used by HostingStack). ---
assert.ok(stack.userPool, 'userPool must be exposed');
assert.ok(stack.userPoolClient, 'userPoolClient must be exposed');
assert.ok(
  stack.hostedUiDomain.includes('auth.ap-northeast-2.amazoncognito.com'),
  `hostedUiDomain must be the full auth domain, got ${stack.hostedUiDomain}`,
);

console.log('OK  auth stack: no-self-signup + alias username + groups + managed login v2 + code-only client + self-service scope + NoEcho seed password param + per-account seed pairing (create/suppress, temporary password via Ref, no reset-on-redeploy, correct group)');

// --- Seed permissions are attached inline when the role is created (IAM eventual consistency) ---
//
// **Observed (2026-08-21, ROLLBACK on the first creation of AipdsAuthStack).** Of the 6 seed calls,
// one, `adminAddUserToGroup` (pm), died with AccessDenied and the stack rolled back:
//
//   01:03:06  PutRolePolicy  SeedPmGroupCustomResourcePolicy    (succeeded)
//   01:03:22  AdminAddUserToGroup  pm     -> AccessDenied
//   01:03:23  AdminAddUserToGroup  admin  -> succeeded
//
// Same role, same action, same resource, same-shaped policies attached at the same time, yet one second apart
// one was denied and one went through — eventual consistency in the IAM authorization path. `DependsOn`
// only guarantees the **order of API calls**, not that IAM has picked up the new policy.
//
// `AwsCustomResourcePolicy.fromSdkCalls` creates a separate `AWS::IAM::Policy` per call, and
// each is written just before it is consumed (16 seconds before, observed) — six calls mean six races,
// and losing just one rolls back the whole stack. As seeds grow, the failure probability rises with them.
//
// So the permissions move into the **role's inline policy**. Then the IAM write happens at `CreateRole`
// time, and that role must exist before the provider Lambda, which must exist before any custom
// resource — in the observed timeline that gives 40-80 seconds of slack. The point is not reducing the races
// from six to zero but **guaranteeing when the policy exists through the order of resource
// creation**.
{
  const seedPolicies = Object.entries(t.findResources('AWS::IAM::Policy'))
    .filter(([id]) => id.startsWith('Seed'));
  assert.deepStrictEqual(
    seedPolicies.map(([id]) => id), [],
    'seed permissions must not come from per-call AWS::IAM::Policy resources — each one '
    + 'is written moments before its consumer runs and races IAM convergence. Found: '
    + seedPolicies.map(([id]) => id).join(', '),
  );

  // The permissions must be in the provider role's inline policy. All three actions — if even one is
  // missing, the same rollback happens at that step.
  const roles = Object.values(t.findResources('AWS::IAM::Role'))
    .filter((r) => JSON.stringify(r.Properties?.Policies ?? '').includes('cognito-idp:'));
  assert.strictEqual(
    roles.length, 1,
    `expected exactly 1 role carrying the seed permissions inline, got ${roles.length}`,
  );
  const statements = (roles[0]!.Properties.Policies as any[])
    .flatMap((p) => p.PolicyDocument.Statement as any[]);
  for (const action of ['cognito-idp:AdminCreateUser',
                        'cognito-idp:AdminSetUserPassword',
                        'cognito-idp:AdminAddUserToGroup']) {
    const hit = statements.find((s) => {
      const a = s.Action;
      return Array.isArray(a) ? a.includes(action) : a === action;
    });
    assert.ok(hit, `seed role must allow ${action} inline at role creation`);
    // Must be narrowed to the pool ARN — with a wildcard, users in other pools could be touched too.
    assert.ok(
      JSON.stringify(hit.Resource).includes('UserPool'),
      `${action} must be scoped to this user pool, got ${JSON.stringify(hit.Resource)}`,
    );
  }
}
console.log('OK  auth stack: seed permissions are inline on the provider role (no per-call policy racing IAM)');
