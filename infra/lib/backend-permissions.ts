import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';

// Runtime default model — user-data exports it as ANTHROPIC_MODEL. It is kept separate
// from permissions: changing this value changes deployment behavior, while growing the
// list below is "leaving it changeable".
export const MODEL = 'global.anthropic.claude-opus-4-8';

// Models allowed to be invoked — a wildcard, not an explicit list.
//
// The problem with the explicit list: when the model catalog moved to S3
// (/admin/models, spec 2026-08-01), admins became able to register new Claude models from
// the screen, but with IAM allowing only 5, registration still led to AccessDenied on the
// first chat turn, a failure that remained only in the backend log. Showing "you can add
// models from the screen" while actually requiring a cdk deploy is the worst state, so we
// widen it to a wildcard — that is the only condition under which the feature holds.
//
// Widening the allowed scope to "every global Anthropic Claude inference profile" is an
// intended trade-off. This role does nothing besides Bedrock invoke (S3 is a separate
// statement), and the data boundary is the same whichever Claude model is called.
//
// inference-profile takes the global.* prefix, foundation-model takes the form without it —
// both are needed (calls via a profile reference the latter internally).
// test/hosting-stack.assert.ts asserts these two patterns and whether MODEL is covered.
// The sandboxed processes' AgentRole (lib/aipds-agent-creds-stack.ts) uses the same list.
export const INVOKABLE_MODEL_ARNS = (account: string) => [
  `arn:aws:bedrock:*:${account}:inference-profile/global.anthropic.claude-*`,
  `arn:aws:bedrock:*::foundation-model/anthropic.claude-*`,
];

// Artifact bucket prefixes the backend uses. Project data lives under projects/ and
// strands sessions under sessions/ — surveys/ and models/ must be outside the project
// prefix.
//
// surveys/by-token/{token}.json is a one-way token -> prototype index. The public
// survey link (/survey/{token}) must read it before knowing which project the token belongs
// to, so it cannot be placed inside projects/{pid}/
// (surveys_root_s3_factory in backend/aipds/app.py).
//
// models/catalog.json is the model list for the project creation screen. It is read when
// there may be no projects at all, so for the same reason it is outside the project prefix
// (CATALOG_KEY in backend/aipds/model_catalog.py).
//
// Measured deployment bug: surveys/* was missing from this list, so every survey creation was
// a 500, and AccessDenied (PutObject on surveys/by-token/...) remained only in the backend log.
// The cause was that this helper was not updated along with the survey feature —
// it pairs with TOKEN_INDEX_PREFIX in backend/aipds/survey/store.py.
// It is needed for ListBucket too: token reclamation in purge() goes through delete_prefix (= list,
// then delete_objects), so without list permission it silently deletes 0 items.
//
// design/profile.json is the brand design profile uploaded by an admin. It is managed even
// with no project, so it is outside the project prefix for the same reason as models/catalog.json
// (DESIGN_PROFILE_KEY in backend/aipds/design_profile.py).
// imports/* is the staging area for project import. It is where the browser uploads the bundle
// **directly** with a presigned PUT, and that URL is signed with this role's credentials, so if
// it is missing here the signing succeeds and S3 gives the browser a 403 — nothing remains in
// the backend log. It is kept outside projects/ so it does not mix with project data: it leaves
// no room for restore_projects' scan or project deletion's delete_prefix to mistake its keys
// for their own (backend/aipds/import_staging.py).
const BACKEND_BUCKET_PREFIXES = [
  'projects/*', 'sessions/*', 'surveys/*', 'models/*', 'design/*', 'imports/*',
] as const;

// When an Anthropic model is called **for the first time** in an account, Bedrock automatically creates an AWS Marketplace
// subscription — if the calling role lacks this permission, it is a 403 ("not authorized to perform the required AWS Marketplace actions").
// We cannot know which role will make a new account's first call, or the first call after adding a new model, so it is granted
// to every role that calls Bedrock (the backend/instance role, the sandbox's AgentRole). Once subscribed, every role in the
// account calls without this permission.
//
// CalledViaLast: subscription happens **only via a Bedrock call** — it cannot call the Marketplace API directly to subscribe
// to other products, and the Bedrock calls it goes through are bound by INVOKABLE_MODEL_ARNS. Product IDs differ per
// model and would need fixing for every new model, so they are not constrained.
export function bedrockSubscribeStatement(): iam.PolicyStatement {
  return new iam.PolicyStatement({
    actions: [
      'aws-marketplace:Subscribe',
      'aws-marketplace:Unsubscribe',
      'aws-marketplace:ViewSubscriptions',
    ],
    resources: ['*'],
    conditions: { StringEquals: { 'aws:CalledViaLast': 'bedrock.amazonaws.com' } },
  });
}

// Common permissions the backend (the drill role or the EC2 instance role) needs:
// Bedrock invoke (+ automatic Marketplace subscription on first call) + read/write/list on the artifact bucket's projects/*, sessions/*, surveys/*,
// models/*, design/*.
export function backendPolicyStatements(
  bucket: s3.IBucket,
  account: string,
): iam.PolicyStatement[] {
  return [
    new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: INVOKABLE_MODEL_ARNS(account),
    }),
    bedrockSubscribeStatement(),
    new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: BACKEND_BUCKET_PREFIXES.map((p) => `${bucket.bucketArn}/${p}`),
    }),
    new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': [...BACKEND_BUCKET_PREFIXES] } },
    }),
  ];
}
