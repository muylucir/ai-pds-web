// Values the stacks pass to the instance (SSM Parameter Store). A new instance reads them at boot
// (infra/scripts/aipds-harden `boot`), and the sync timer picks up the ones written later.
//
// **Why not put the stack outputs into user-data.** Both stacks reference HostingStack's instance role —
// if user-data referenced their outputs that would be a cycle, and changing user-data to break it makes
// the HostingStack deploy replace the EC2 instance. So the stacks write the values as parameters and the
// instance reads them at run time. Without the stack there is no parameter, and boot leaves that
// feature off.
//
// HostingStack's own app origin comes this way for the same kind of reason: its distribution reaches the
// instance through a VPC origin, so the instance's user-data cannot also reference the distribution.
//
// The names also exist as constants in aipds-harden — test/instance-params.assert.ts keeps the two in sync.
export const INSTANCE_PARAMS = {
  /** HostingStack's app origin (https://<distribution>). The frontend's APP_BASE_URL — login is built on it. */
  appOrigin: '/aipds/app-origin',
  /** AipdsAgentCredsStack's AgentRole. If present, boot turns on the wrapper and blocks IMDS. */
  agentRoleArn: '/aipds/agent-role-arn',
  /** AipdsPreviewStack's preview origin and the ARN of the secret-header secret. */
  previewOrigin: '/aipds/preview-origin',
  previewSecretArn: '/aipds/preview-secret-arn',
} as const;
