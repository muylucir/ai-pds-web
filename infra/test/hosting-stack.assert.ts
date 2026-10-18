import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import {
  AipdsDrillStack, IMPORT_STAGING_EXPIRY_DAYS, LOCAL_UPLOAD_ORIGIN,
} from '../lib/aipds-drill-stack';
import { AipdsHostingStack } from '../lib/aipds-hosting-stack';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import { MODEL } from '../lib/backend-permissions';
import {
  ACCESS_TOKEN_VALIDITY_MINUTES, ID_TOKEN_VALIDITY_MINUTES,
  OAUTH_SCOPES, REFRESH_TOKEN_VALIDITY_MINUTES,
} from '../lib/auth-client-config';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

// Every prefix the backend uses in the artifact bucket. Must match
// BACKEND_BUCKET_PREFIXES in lib/backend-permissions.ts. surveys/* is needed for the
// token index (surveys/by-token/) outside the project prefix — a value the public
// survey link must read before it knows which project the token belongs to. models/* is
// for the model catalog (models/catalog.json), outside the project prefix for the same reason.
// design/* is the brand profile uploaded by an admin (design/profile.json) — it is outside
// the project prefix for the same reason as models/* (it is managed even with no project).
// imports/* is the staging area for project import. If it is missing, the presigned PUT the
// backend signed becomes a 403 in the browser, and since S3 issues that rejection to the
// browser, the backend log has no trace of it — a quieter failure than a missing surveys/*.
const BUCKET_PREFIXES = ['projects/*', 'sessions/*', 'surveys/*', 'models/*', 'design/*', 'imports/*'];

/** Verify that object permissions (Get/Put/Delete) and list permissions (ListBucket)
 *  cover BUCKET_PREFIXES without gaps. An assertion that only checks the action exists
 *  misses a missing prefix, and that gap turns only the affected feature into a 500. */
function assertBucketPrefixesCovered(t: Template) {
  const rendered = JSON.stringify(t.findResources('AWS::IAM::Policy'));
  for (const prefix of BUCKET_PREFIXES) {
    // The object statement's Resource is assembled as "<bucketArn>/projects/*", so we
    // search including the leading slash — distinct from the ListBucket condition value ("projects/*").
    assert.ok(
      rendered.includes(`/${prefix}`),
      `s3 object permissions must cover ${prefix} (backend writes there)`,
    );
    assert.ok(
      rendered.includes(`"${prefix}"`),
      `s3:ListBucket prefix condition must cover ${prefix}`,
    );
  }
}

function testDrillUnchanged() {
  const app = new cdk.App();
  const drill = new AipdsDrillStack(app, 'Drill', { env: ENV });
  const t = Template.fromStack(drill);

  // Bedrock invoke statement exists.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream']),
        }),
      ]),
    },
  });
  assertFirstCallSubscription(t);  // drill backend role
  // S3 object Get/Put/Delete statement exists.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['s3:GetObject', 's3:PutObject', 's3:DeleteObject']),
        }),
      ]),
    },
  });
  // S3 ListBucket statement + BUCKET_PREFIXES prefix condition exist.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          // A single action is collapsed to a scalar string, not an array, during CFN synthesis.
          Action: 's3:ListBucket',
          Condition: {
            StringLike: {
              's3:prefix': Match.arrayWith([...BUCKET_PREFIXES]),
            },
          },
        }),
      ]),
    },
  });
  // Every prefix must be present in both the object permissions and the list permissions.
  //
  // The arrayWith assertions above were not enough — they only check "this action
  // exists" and never look at resources, so they passed even with surveys/* missing
  // from the object statement. That gap showed up in a real deployment as a survey
  // creation 500 (AccessDenied: PutObject on surveys/by-token/...). When one prefix the
  // backend uses is missing, only that feature becomes a 500 and the screen shows no
  // cause, so we check prefix by prefix here.
  assertBucketPrefixesCovered(t);
  // Exactly one bucket exposed.
  assert.ok(drill.artifactsBucket, 'artifactsBucket must be exposed');
  t.resourceCountIs('AWS::S3::Bucket', 1);
  console.log('OK  drill stack: bedrock + s3 object/list on every backend prefix + bucket exposed');
}

/** Conditions for import uploads to work from the browser: bucket CORS and staging lifetime.
 *
 *  Without CORS, the preflight is blocked, and that failure is visible only in the browser
 *  console — the backend issued the signature, so it believes it did its part. */
function testImportUploadWiring() {
  const app = new cdk.App();
  const drill = new AipdsDrillStack(app, 'DrillCors', {
    env: ENV,
    uploadOrigins: ['https://d111111abcdef8.cloudfront.net'],
  });
  const t = Template.fromStack(drill);

  t.hasResourceProperties('AWS::S3::Bucket', {
    CorsConfiguration: {
      CorsRules: [Match.objectLike({
        AllowedMethods: ['PUT'],
        AllowedOrigins: [LOCAL_UPLOAD_ORIGIN, 'https://d111111abcdef8.cloudfront.net'],
        // These headers are included in the signature — if they are not on the allowlist, the preflight blocks.
        AllowedHeaders: ['content-type'],
      })],
    },
  });

  // A wildcard origin is an accident: it becomes a surface for another site's scripts to
  // reuse the user's valid presigned URL.
  const rendered = JSON.stringify(t.findResources('AWS::S3::Bucket'));
  assert.ok(!rendered.includes('"*"'),
    'bucket CORS must never allow a wildcard origin');

  // Abandoned staging goes away. Without this, objects the size of a whole project quietly pile up.
  t.hasResourceProperties('AWS::S3::Bucket', {
    LifecycleConfiguration: {
      Rules: Match.arrayWith([Match.objectLike({
        Prefix: 'imports/',
        Status: 'Enabled',
        ExpirationInDays: IMPORT_STAGING_EXPIRY_DAYS,
      })]),
    },
  });
  console.log('OK  drill stack: bucket CORS (PUT, no wildcard) + imports/ lifecycle');
}

/** Synthesis works without passing a deployment domain, and only localhost is allowed — the state of a first deployment. */
function testUploadOriginsDefaultToLocalOnly() {
  const app = new cdk.App();
  const t = Template.fromStack(new AipdsDrillStack(app, 'DrillLocal', { env: ENV }));

  t.hasResourceProperties('AWS::S3::Bucket', {
    CorsConfiguration: {
      CorsRules: [Match.objectLike({ AllowedOrigins: [LOCAL_UPLOAD_ORIGIN] })],
    },
  });
  console.log('OK  drill stack: upload origins default to localhost only');
}

testDrillUnchanged();
testImportUploadWiring();
testUploadOriginsDefaultToLocalOnly();

function makeHosting() {
  const app = new cdk.App();
  const drill = new AipdsDrillStack(app, 'Drill2', { env: ENV });
  const auth = new AipdsAuthStack(app, 'Auth2', { env: ENV });
  const stack = new AipdsHostingStack(app, 'Hosting', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-test0000',   // injected → bypasses fromLookup (no credentials needed)
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  return Template.fromStack(stack);
}

function testNetworkAndSecret() {
  const t = makeHosting();
  // Ingress on 80 uses only the prefix list source, with no open CIDR.
  t.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
    IpProtocol: 'tcp', FromPort: 80, ToPort: 80, SourcePrefixListId: 'pl-test0000',
  });
  // No ingress on 22 (SSH) at all.
  const ingresses = t.findResources('AWS::EC2::SecurityGroupIngress');
  for (const [, r] of Object.entries(ingresses)) {
    assert.notStrictEqual((r as any).Properties.FromPort, 22, 'no SSH ingress allowed');
  }
  // No 0.0.0.0/0 ingress (and no separate inline ingress either).
  const sgs = t.findResources('AWS::EC2::SecurityGroup');
  for (const [, r] of Object.entries(sgs)) {
    const inline = (r as any).Properties.SecurityGroupIngress ?? [];
    for (const rule of inline) {
      assert.notStrictEqual(rule.CidrIp, '0.0.0.0/0', 'no open CIDR ingress');
    }
  }
  // Zero NAT gateways.
  t.resourceCountIs('AWS::EC2::NatGateway', 0);
  // The secret-header secret exists (generated without punctuation).
  t.hasResourceProperties('AWS::SecretsManager::Secret', {
    GenerateSecretString: Match.objectLike({ ExcludePunctuation: true }),
  });
  console.log('OK  hosting: SG prefix-list only, no SSH, secret present');
}

testNetworkAndSecret();

function testComputeAndRole() {
  const t = makeHosting();
  // One x86_64 instance, IMDSv2 enforced (HttpTokens required). Graviton is not
  // used — the SDK's bundled binary is an x86-64 ELF, and the native npm modules
  // prototypes install also fetch x86_64 prebuilts.
  t.hasResourceProperties('AWS::EC2::Instance', {
    InstanceType: 'm7i.2xlarge',
  });
  t.hasResourceProperties('AWS::EC2::LaunchTemplate', {
    LaunchTemplateData: Match.objectLike({
      MetadataOptions: Match.objectLike({ HttpTokens: 'required' }),
    }),
  });
  // With builds moving onto this box, node_modules stays resident per prototype — 20GB is
  // not enough.
  t.hasResourceProperties('AWS::EC2::Instance', {
    BlockDeviceMappings: Match.arrayWith([
      Match.objectLike({ Ebs: Match.objectLike({ VolumeSize: 100 }) }),
    ]),
  });
  // EIP exists + is associated.
  t.resourceCountIs('AWS::EC2::EIP', 1);
  t.resourceCountIs('AWS::EC2::EIPAssociation', 1);
  // Instance role: Bedrock + secret read + SSM managed policy.
  t.hasResourceProperties('AWS::IAM::Role', {
    ManagedPolicyArns: Match.arrayWith([
      Match.objectLike({
        'Fn::Join': Match.arrayWith([
          Match.arrayWith([Match.stringLikeRegexp('AmazonSSMManagedInstanceCore')]),
        ]),
      }),
    ]),
  });
  const policies = t.findResources('AWS::IAM::Policy');
  const allActions = JSON.stringify(policies);
  assert.match(allActions, /secretsmanager:GetSecretValue/, 'instance role reads header secret');
  assert.match(allActions, /bedrock:InvokeModel/, 'instance role invokes bedrock');
  assertFirstCallSubscription(t);  // instance role
  // The instance role is the role that actually produced AccessDenied in deployment — it uses
  // the same helper as the drill role, but we check prefixes here too. If only one of the two
  // stacks is checked, the gap is silently missed again the moment the call sites diverge.
  assertBucketPrefixesCovered(t);
  // Model access is a wildcard, not an explicit list. With an explicit list, even after an
  // admin registers a new model in /admin/models, IAM blocks it and the first chat turn gets
  // AccessDenied, a failure that remains only in the backend log — showing "you can add
  // models from the screen" while actually requiring a cdk deploy is the worst state.
  // (spec 2026-08-01-per-project-model-selection §4)
  assert.match(allActions, /inference-profile\/global\.anthropic\.claude-\*/,
    'instance role can invoke any global Anthropic Claude inference profile');
  assert.match(allActions, /foundation-model\/anthropic\.claude-\*/,
    'instance role can invoke any Anthropic Claude foundation model');
  // Whether the fallback default is actually covered by that wildcard. MODEL is not in the
  // catalog's seed list (it does not show in the combo box), but it remains as the fallback
  // for old projects and unspecified models, so it must be invocable.
  assert.ok(MODEL.startsWith('global.anthropic.claude-'),
    `MODEL ${MODEL} must fall under the global.anthropic.claude-* wildcard`);
  // lambda-microvms control permissions went away along with the VM layer.
  if (allActions.includes('lambda-microvms')) {
    throw new Error('hosting: instance role still carries lambda-microvms permissions');
  }
  console.log('OK  hosting: EC2 x86_64 + 100GB EBS + EIP + instance role (bedrock/secret/ssm, no microvm)');
}

testComputeAndRole();

function testCloudFront() {
  const t = makeHosting();
  t.hasResourceProperties('AWS::CloudFront::Distribution', {
    DistributionConfig: Match.objectLike({
      Origins: Match.arrayWith([
        Match.objectLike({
          CustomOriginConfig: Match.objectLike({ OriginProtocolPolicy: 'http-only' }),
          OriginCustomHeaders: Match.arrayWith([
            Match.objectLike({ HeaderName: 'X-Origin-Verify' }),
          ]),
        }),
      ]),
      DefaultCacheBehavior: Match.objectLike({
        ViewerProtocolPolicy: 'redirect-to-https',
      }),
    }),
  });
  // The /_next/static/* cache behavior exists.
  const dists = t.findResources('AWS::CloudFront::Distribution');
  const cfg = Object.values(dists)[0] as any;
  const behaviors = cfg.Properties.DistributionConfig.CacheBehaviors ?? [];
  assert.ok(
    behaviors.some((b: any) => b.PathPattern === '/_next/static/*'),
    'static cache behavior present',
  );
  console.log('OK  hosting: CloudFront http-only origin + X-Origin-Verify header + https redirect');
}

testCloudFront();

// The Create/Update fields of Custom::AWS are JSON describing the SDK call, assembled with
// Fn::Join (only the reference parts are { Ref: ... } / { Fn::GetAtt: ... } etc.) —
// concatenating the string fragments, replacing references with placeholders, and running
// JSON.parse yields the actual service/action/parameters. Only then can we verify not just
// "are all fields present" but "are the values right" (e.g., whether AllowedOAuthFlows is
// exactly ['code'] with no ['implicit'] mixed in) — a string containment check
// (bodies.includes(...)) passes as long as the field name is there, even if the value differs.
function parseSdkPayload(field: any): { service: string; action: string; parameters: any } {
  const parts = field['Fn::Join'][1];
  const joined = parts.map((p: any) => (typeof p === 'string' ? p : '__REF__')).join('');
  return JSON.parse(joined);
}

// --- Callback URL injection (breaking the circular dependency) ---
{
  const app = new cdk.App();
  const drill = new AipdsDrillStack(app, 'Drill3', { env: ENV });
  const auth = new AipdsAuthStack(app, 'Auth3', { env: ENV });
  const hosting = new AipdsHostingStack(app, 'Hosting3', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-1234',
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);
  const customResources = t.findResources('Custom::AWS');
  const resourceList = Object.values(customResources);
  const target = resourceList.find((r: any) => {
    try {
      return parseSdkPayload(r.Properties.Create).action === 'updateUserPoolClient';
    } catch {
      return false;
    }
  }) as any;
  assert.ok(target, 'hosting must register the CloudFront callback URL with the app client');

  // Both onCreate and onUpdate must exist — without Update, the callback is not refreshed
  // on redeploy and stays stale even when the domain changes. The two objects share the
  // same reference (one shared updateClientCall), so with only Create present the
  // Create-side string checks all pass — hence we separately check that the Update field 'exists' at all.
  assert.ok(target.Properties.Update, 'onUpdate must be set — otherwise a redeploy never refreshes stale callbacks');

  const createPayload = parseSdkPayload(target.Properties.Create);
  const updatePayload = parseSdkPayload(target.Properties.Update);
  assert.strictEqual(updatePayload.action, 'updateUserPoolClient', 'onUpdate must call the same SDK action as onCreate');

  const params = createPayload.parameters;

  // It has PUT semantics, so the whole configuration must be rewritten — we check the values,
  // not just that the fields exist. A check that only looks for the presence of
  // 'AllowedOAuthFlows' cannot catch ['implicit'] slipping in.
  assert.deepStrictEqual(params.AllowedOAuthFlows, ['code'],
    'UpdateUserPoolClient has PUT semantics — must resend the exact flow, not merely "a" flow');
  assert.strictEqual(params.AllowedOAuthFlowsUserPoolClient, true);
  // The list is pulled from the constant instead of being written here again — if a scope is
  // added without fixing this resend too, PUT semantics silently erase that scope on every redeploy.
  // When `aws.cognito.signin.user.admin` disappears that way, changing one's own password dies with
  // "Access Token does not have required scopes", and the cause is this resend.
  assert.deepStrictEqual(
    [...params.AllowedOAuthScopes].sort(), [...OAUTH_SCOPES].sort(),
    'OAuth scopes must be resent',
  );
  assert.deepStrictEqual(params.SupportedIdentityProviders, ['COGNITO']);
  assert.strictEqual(params.PreventUserExistenceErrors, 'ENABLED');
  assert.strictEqual(params.EnableTokenRevocation, true);
  // Token validity + refresh auth flow — fields that were missing from the first implementation.
  // They must be exactly the same values as AuthStack's client definition: if even one is
  // missing, that field is silently reset to the Cognito default on every redeploy.
  //
  // References the constants in auth-client-config (no hard-coded literals). With literals, it
  // is possible to change only the constant when adjusting validity and forget this file, and
  // what breaks then is the very invariant this test guards: "the two stacks have diverged".
  assert.strictEqual(params.AccessTokenValidity, ACCESS_TOKEN_VALIDITY_MINUTES);
  assert.strictEqual(params.IdTokenValidity, ID_TOKEN_VALIDITY_MINUTES);
  assert.strictEqual(params.RefreshTokenValidity, REFRESH_TOKEN_VALIDITY_MINUTES);
  assert.deepStrictEqual(params.TokenValidityUnits, {
    AccessToken: 'minutes', IdToken: 'minutes', RefreshToken: 'minutes',
  });
  assert.deepStrictEqual(params.ExplicitAuthFlows, ['ALLOW_REFRESH_TOKEN_AUTH'],
    'the /api proxy 401-refresh path depends on this grant — dropping it silently breaks session renewal on redeploy');

  // The localhost callback must remain too, or local development breaks.
  assert.ok(params.CallbackURLs.includes('http://localhost:3000/api/auth/callback'),
    'the localhost callback must survive the update');
  assert.ok(params.LogoutURLs.includes('http://localhost:3000/login'),
    'the localhost logout URL must survive the update');

  // --- Drift detection: comparing each AuthStack client property by hand is not a
  // repeatable defense (a reviewer had to read the CDK source to find the missing ClientName).
  // So we actually synthesize AuthStack, take every CFN property key its client resource
  // has, and mechanically check that the resend parameters have all of them — the next
  // time a field is added to addClient() and the resend is not fixed, this assertion fails
  // immediately, naming that field.
  const authTemplate = Template.fromStack(auth);
  const authClients = authTemplate.findResources('AWS::Cognito::UserPoolClient');
  const authClientProps = (Object.values(authClients)[0] as any).Properties;

  // Fields that legitimately differ:
  //  - CallbackURLs/LogoutURLs: the resend deliberately appends the CloudFront domain,
  //    so differing from AuthStack's localhost-only value is expected (the survival of the
  //    localhost callback was already checked separately above).
  //  - UserPoolId/ClientId: these are address values that designate "which client" this
  //    resource is, not configuration — the AuthStack-side UserPoolId is a { Ref: ... }
  //    token and the resend side is an actual string, so even their forms differ.
  //  - GenerateSecret: a field only CreateUserPoolClient has (whether there is a secret
  //    cannot be changed after creation) — UpdateUserPoolClient's request syntax has no
  //    such field, so sending it here would actually make the API reject the request.
  const legitimatelyDifferent = new Set([
    'CallbackURLs', 'LogoutURLs', 'UserPoolId', 'ClientId', 'GenerateSecret',
  ]);
  for (const key of Object.keys(authClientProps)) {
    if (legitimatelyDifferent.has(key)) continue;
    assert.ok(
      Object.prototype.hasOwnProperty.call(params, key),
      `AuthStack's client sets '${key}' but the UpdateUserPoolClient resend does not — ` +
      `PUT semantics will reset it to a Cognito default on the next deploy`,
    );
  }

  // The instance role must be able to read the client secret (looked up at boot).
  // The action sits in an array in the same statement as the Admin* permissions for /admin/users —
  // the full list is pinned below in "instance role has every Cognito Admin action".
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['cognito-idp:DescribeUserPoolClient']),
        }),
      ]),
    },
  });
  console.log('OK  hosting stack: callback URL injection + full client config resend (incl. token validity/refresh flow) + onUpdate present + secret read permission');
}

// --- Non-ASCII characters must not leak into resource properties ---
// Measured deployment failure: EC2 rejects non-ASCII in a SecurityGroup's GroupDescription
// ("Character sets beyond ASCII are not supported"). The habit of Korean comments carried
// straight into description/comment strings, and an em dash (—) slipped in. Synthesis passes,
// and it dies at the point CloudFormation calls the API.
//
// Cognito group descriptions (in Korean) actually deploy successfully, so a blanket ban is
// excessive — we check only the properties of services that enforce ASCII. Comments are not
// in scope (they do not go into the template).
{
  const app = new cdk.App();
  const drill = new AipdsDrillStack(app, 'Drill4', { env: ENV });
  const auth = new AipdsAuthStack(app, 'Auth4', { env: ENV });
  const hosting = new AipdsHostingStack(app, 'Hosting4', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);

  const nonAscii = /[^\x00-\x7F]/;

  // EC2 SecurityGroup: GroupDescription + inbound/outbound rule descriptions.
  for (const [id, sg] of Object.entries(t.findResources('AWS::EC2::SecurityGroup'))) {
    const p: any = (sg as any).Properties ?? {};
    for (const field of ['GroupDescription', 'GroupName']) {
      const v = p[field];
      if (typeof v === 'string') {
        assert.ok(!nonAscii.test(v),
          `${id}.${field} must be ASCII-only (EC2 rejects non-ASCII): ${JSON.stringify(v)}`);
      }
    }
    for (const key of ['SecurityGroupIngress', 'SecurityGroupEgress']) {
      for (const rule of (p[key] ?? []) as any[]) {
        if (typeof rule?.Description === 'string') {
          assert.ok(!nonAscii.test(rule.Description),
            `${id}.${key}[].Description must be ASCII-only: ${JSON.stringify(rule.Description)}`);
        }
      }
    }
  }
  // Rules that fall out as separate resources are subject to the same constraint.
  for (const type of ['AWS::EC2::SecurityGroupIngress', 'AWS::EC2::SecurityGroupEgress']) {
    for (const [id, r] of Object.entries(t.findResources(type))) {
      const d = ((r as any).Properties ?? {}).Description;
      if (typeof d === 'string') {
        assert.ok(!nonAscii.test(d), `${id}.Description must be ASCII-only: ${JSON.stringify(d)}`);
      }
    }
  }
  // CloudFront Comment — for console display, and ASCII safety has not been confirmed.
  for (const [id, d] of Object.entries(t.findResources('AWS::CloudFront::Distribution'))) {
    const c = ((d as any).Properties ?? {}).DistributionConfig?.Comment;
    if (typeof c === 'string') {
      assert.ok(!nonAscii.test(c), `${id} Comment must be ASCII-only: ${JSON.stringify(c)}`);
    }
  }
  console.log('OK  hosting: ASCII-only SG descriptions + CloudFront comment (EC2 rejects non-ASCII)');
}

// --- Cognito Admin API permissions used by /admin/users ---
// Measured deployment bug: login worked but /admin/users returned 502. Backend log:
// "cognito call failed (AccessDeniedException) -> 502". The instance role had only
// DescribeUserPoolClient, for reading the secret at boot, and no user management API
// permissions at all.
//
// Pins every action backend/aipds/auth/cognito.py actually calls — if even one is
// missing, only that feature becomes a 502, and the screen shows no cause.
{
  const app = new cdk.App();
  const drill = new AipdsDrillStack(app, 'Drill5', { env: ENV });
  const auth = new AipdsAuthStack(app, 'Auth5', { env: ENV });
  const hosting = new AipdsHostingStack(app, 'Hosting5', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);

  // Every _call() invocation in cognito.py (10) + DescribeUserPoolClient for boot.
  const required = [
    'cognito-idp:AdminAddUserToGroup',
    'cognito-idp:AdminCreateUser',
    'cognito-idp:AdminDeleteUser',
    'cognito-idp:AdminDisableUser',
    'cognito-idp:AdminEnableUser',
    'cognito-idp:AdminListGroupsForUser',
    'cognito-idp:AdminRemoveUserFromGroup',
    'cognito-idp:AdminSetUserPassword',
    'cognito-idp:DescribeUserPoolClient',
    'cognito-idp:ListUsers',
    'cognito-idp:ListUsersInGroup',
  ];

  // Collect cognito-idp actions from every policy statement attached to the instance role.
  const granted = new Set<string>();
  for (const policy of Object.values(t.findResources('AWS::IAM::Policy'))) {
    for (const stmt of ((policy as any).Properties?.PolicyDocument?.Statement ?? [])) {
      const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
      for (const a of actions) {
        if (typeof a === 'string' && a.startsWith('cognito-idp:')) granted.add(a);
      }
    }
  }
  for (const action of required) {
    assert.ok(granted.has(action),
      `instance role must allow ${action} — /admin/users 502s with AccessDeniedException without it`);
  }
  // Not mashed into a wildcard: grant only what is needed (least privilege).
  assert.ok(!granted.has('cognito-idp:*'),
    'do not grant cognito-idp:* — list the actions the backend actually calls');
  console.log('OK  hosting: instance role has every Cognito Admin action /admin/users calls');
}

// --- Launch template names must be unique account-wide ---
//
// **Measured (2026-08-21, first creation of AipdsHostingStack failed).**
//
//   CREATE_FAILED  AWS::EC2::LaunchTemplate  InstanceLaunchTemplate
//   "Launch template name already in use." (InvalidLaunchTemplateName.AlreadyExistsException)
//
// `requireImdsv2: true` turns on CDK's InstanceRequireImdsv2Aspect. That aspect creates the LT
// and names it like this (aws-cdk-lib/aws-ec2/lib/aspects/require-imdsv2-aspect):
//
//   feature flag set   -> Names.uniqueId(launchTemplate)   (unique per stack/path)
//   feature flag unset -> `${node.node.id}LaunchTemplate`   (fixed!)
//
// If the flag (`@aws-cdk/aws-ec2:uniqueImdsv2TemplateName`) is not in cdk.json, the name is
// fixed as `InstanceLaunchTemplate`, and that is an **account/region-wide namespace**. In fact,
// an untagged orphan LT created on 2026-07-26 (a leftover from an old deployment) held that
// name, and the first deployment died. For this product, which shares accounts in workshops, per-stack uniqueness is a requirement.
{
  // **Actually read cdk.json's context and put it into the App.** `new cdk.App()` alone does not
  // bring in feature flags — it is the CDK CLI that injects them. So the other blocks in this
  // file check a **different synthesis configuration** from what is deployed, and this defect
  // slipped through that gap. This block reads the source of truth (cdk.json), so removing the flag makes it fail.
  const cdkJson = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', 'cdk.json'), 'utf8'));
  const app = new cdk.App({ context: cdkJson.context });
  const drill = new AipdsDrillStack(app, 'Drill6', { env: ENV });
  const auth = new AipdsAuthStack(app, 'Auth6', { env: ENV });
  const hosting = new AipdsHostingStack(app, 'Hosting6', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);

  const lts = Object.entries(t.findResources('AWS::EC2::LaunchTemplate'));
  assert.strictEqual(lts.length, 1, `expected 1 launch template, got ${lts.length}`);
  const name = lts[0]![1].Properties?.LaunchTemplateName;
  assert.ok(typeof name === 'string' && name.length > 0,
    `LaunchTemplateName must be set, got ${JSON.stringify(name)}`);
  assert.notStrictEqual(
    name, 'InstanceLaunchTemplate',
    'LaunchTemplateName is the collision-prone legacy default — enable the feature flag '
    + '"@aws-cdk/aws-ec2:uniqueImdsv2TemplateName" in cdk.json. This exact name already '
    + 'failed a deploy: an orphaned launch template in the account held it.',
  );
  // The stack name must be included so that two deployments sharing an account do not block each other.
  assert.ok(
    name.includes('Hosting6'),
    `LaunchTemplateName must be scoped by stack to stay unique per deploy, got ${name}`,
  );
}
console.log('OK  hosting: launch template name is unique per stack (no account-wide collision)');

// For an Anthropic model's first call, Bedrock automatically creates a Marketplace subscription — if the calling role lacks
// permission, it is a 403 (lib/backend-permissions.ts bedrockSubscribeStatement). Subscription happens only through Bedrock.
function assertFirstCallSubscription(t: Template) {
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([Match.objectLike({
        Action: ['aws-marketplace:Subscribe', 'aws-marketplace:Unsubscribe',
                 'aws-marketplace:ViewSubscriptions'],
        Condition: { StringEquals: { 'aws:CalledViaLast': 'bedrock.amazonaws.com' } },
      })]),
    },
  });
}
