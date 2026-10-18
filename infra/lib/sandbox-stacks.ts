import * as cdk from 'aws-cdk-lib';
import { AipdsHostingStack } from './aipds-hosting-stack';
import { AipdsPreviewStack } from './aipds-preview-stack';
import { AipdsAgentCredsStack } from './aipds-agent-creds-stack';
import { AipdsDrillStack } from './aipds-drill-stack';
import { AipdsUploadCorsStack } from './aipds-upload-cors-stack';

// The stacks for what the instance turns on by itself after boot: the origin dedicated to prototype previews
// (aipds-preview-stack.ts), the Bedrock-only role for sandboxed processes (aipds-agent-creds-stack.ts), and the permission
// to align the bucket CORS (aipds-upload-cors-stack.ts). All three only reference HostingStack's resources (instance role, header secret,
// EIP DNS), and the instance applies them with infra/scripts/aipds-harden sync.
//
// There are two ways the values are received.
//   - **New environment (default):** received from HostingStack through cross-stack references. A single `cdk deploy --all` does it —
//     the instance boots first along with HostingStack, but once the two stacks write their values to SSM, within a few minutes it turns on
//     the wrapper, IMDS block and preview by itself.
//   - **Already-running environment (pinned):** the values are pinned through env. Cross-stack references would tie the two stacks to
//     HostingStack, making `cdk deploy AipdsAgentCredsStack` deploy HostingStack too — and that deploy can replace
//     the EC2 instance (AMI not pinned, userDataCausesReplacement). Pinned, only the two stacks are deployed.
//     (A stack policy refusing replacement and deletion is also put on the running HostingStack — "인스턴스를 새로 만들기" (rebuilding the instance) in /manual.)

export interface PinnedHosting {
  instanceRoleArn: string;
  originDnsName: string;
  originVerifySecretArn: string;
  /** Artifact bucket name. Received as a reference it would be tied to DrillStack — pinned for the same reason. */
  artifactsBucket: string;
}

const PINNED_ENV = {
  instanceRoleArn: 'AIPDS_INSTANCE_ROLE_ARN',
  originDnsName: 'AIPDS_PREVIEW_ORIGIN_DNS',
  originVerifySecretArn: 'AIPDS_ORIGIN_VERIFY_SECRET_ARN',
  artifactsBucket: 'AIPDS_ARTIFACTS_BUCKET',
} as const;

/** The pinned values from env. If any is present, all must be — mixing would tie only some stacks to another stack. */
export function pinnedFromEnv(e: NodeJS.ProcessEnv): PinnedHosting | undefined {
  const values = Object.fromEntries(
    Object.entries(PINNED_ENV).map(([key, name]) => [key, e[name]])) as Record<string, string | undefined>;
  const set = Object.values(values).filter((v) => v);
  if (set.length === 0) return undefined;
  if (set.length !== Object.keys(PINNED_ENV).length) {
    throw new Error(`${Object.values(PINNED_ENV).join(', ')} pin a running environment together `
      + '— set all or none');
  }
  return values as unknown as PinnedHosting;
}

export function addSandboxStacks(app: cdk.App, env: cdk.Environment, drill: AipdsDrillStack,
                                 hosting: AipdsHostingStack, pinned?: PinnedHosting) {
  const instanceRoleArn = pinned?.instanceRoleArn ?? hosting.instanceRole.roleArn;
  const preview = new AipdsPreviewStack(app, 'AipdsPreviewStack', {
    env,
    originDnsName: pinned?.originDnsName ?? hosting.originDnsName,
    originVerifySecretArn: pinned?.originVerifySecretArn ?? hosting.originVerifySecret.secretArn,
    instanceRoleArn,
  });
  const agentCreds = new AipdsAgentCredsStack(app, 'AipdsAgentCredsStack', { env, instanceRoleArn });
  const uploadCors = new AipdsUploadCorsStack(app, 'AipdsUploadCorsStack', {
    env,
    instanceRoleArn,
    bucketArn: pinned ? `arn:aws:s3:::${pinned.artifactsBucket}` : drill.artifactsBucket.bucketArn,
  });
  return { preview, agentCreds, uploadCors };
}
