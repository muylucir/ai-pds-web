#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AipdsDrillStack } from '../lib/aipds-drill-stack';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import { AipdsHostingStack } from '../lib/aipds-hosting-stack';
import { addSandboxStacks, pinnedFromEnv } from '../lib/sandbox-stacks';

const app = new cdk.App();

// Region priority: CDK_DEPLOY_REGION > CDK_DEFAULT_REGION (profile) > Seoul.
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { region, account };

// **Additional** origins (e.g., a custom domain) allowed to PUT directly from the browser to the artifact
// bucket (presigned uploads for project import). The app's own origin (the hosting stack's CloudFront) is
// not passed here — that stack depends on this bucket, so it would be circular, and the instance adds
// it to the bucket CORS itself (lib/aipds-upload-cors-stack.ts, infra/scripts/aipds-harden sync).
//
//   AIPDS_UPLOAD_ORIGINS=https://pds.example.com npx cdk deploy --all
const uploadOrigins = (process.env.AIPDS_UPLOAD_ORIGINS ?? '')
  .split(',')
  .map((o) => o.trim())
  .filter((o) => o.length > 0);

const drill = new AipdsDrillStack(app, 'AipdsDrillStack', { env, uploadOrigins });

// Auth stack: User Pool, groups, Hosted UI v2, app client, 2 seed accounts.
// It deploys with only localhost callback URLs, and the real CloudFront domain is appended by the
// hosting stack below via UpdateUserPoolClient (resolving the circular dependency).
const auth = new AipdsAuthStack(app, 'AipdsAuthStack', { env });

// AZs for the hosting VPC (default: the region's first two). Needed only in the regions where a VPC
// origin is not offered in some AZ — README "Another region". AZ names map to different AZs per account,
// so this cannot be decided in code.
//
//   AIPDS_AVAILABILITY_ZONES=ap-northeast-1a,ap-northeast-1c npx cdk deploy --all
const availabilityZones = (process.env.AIPDS_AVAILABILITY_ZONES ?? '')
  .split(',')
  .map((z) => z.trim())
  .filter((z) => z.length > 0);

// The hosting stack automatically looks up the CloudFront origin-facing prefix list in the deploy region
// (fromLookup) — credentials are needed at synth/deploy time, and the result is cached in
// cdk.context.json (to be committed).
const hosting = new AipdsHostingStack(app, 'AipdsHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.userPoolClient,
  hostedUiDomain: auth.hostedUiDomain,
  availabilityZones,
});

// The preview origin and the Bedrock-only role for sandboxes. New environments create them by reference from
// HostingStack; running environments with values pinned via env (lib/sandbox-stacks.ts — without pinning, HostingStack gets deployed too).
//
//   AIPDS_INSTANCE_ROLE_ARN=<InstanceRole ARN> AIPDS_VPC_ORIGIN_ID=<VpcOriginId> \
//   AIPDS_ORIGIN_PRIVATE_DNS=<OriginPrivateDns> AIPDS_ORIGIN_VERIFY_SECRET_ARN=<OriginVerifyHeader ARN> AIPDS_ARTIFACTS_BUCKET=<bucket> \
//     npx cdk deploy AipdsAgentCredsStack AipdsPreviewStack AipdsUploadCorsStack
addSandboxStacks(app, env, drill, hosting, pinnedFromEnv(process.env));
