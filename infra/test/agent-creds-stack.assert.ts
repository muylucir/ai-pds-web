import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { AipdsAgentCredsStack } from '../lib/aipds-agent-creds-stack';
import { INSTANCE_PARAMS } from '../lib/instance-params';

// The AgentRole contract (header of lib/aipds-agent-creds-stack.ts):
//   - Only the instance role can AssumeRole it. That permission is a separate policy attached to the instance role.
//   - Nothing but Bedrock invoke and the Marketplace auto-subscription on the first call through Bedrock (no S3, Cognito, or Secrets Manager).
//   - It creates no instance-side resources.

const ENV = { account: '123456789012', region: 'ap-northeast-2' };
const ROLE = 'arn:aws:iam::123456789012:role/AipdsHostingStack-InstanceRole3CCE2F1D-QYiZURiI2lpO';

const app = new cdk.App();
const stack = new AipdsAgentCredsStack(app, 'AgentCreds', { env: ENV, instanceRoleArn: ROLE });
const t = Template.fromStack(stack);

for (const type of ['AWS::EC2::Instance', 'AWS::EC2::VPC', 'AWS::EC2::EIP']) {
  assert.deepStrictEqual(t.findResources(type), {}, `agent creds stack must not create ${type}`);
}

const roles = Object.values(t.findResources('AWS::IAM::Role')) as any[];
assert.strictEqual(roles.length, 1, 'exactly one role: AgentRole');
const trust = roles[0].Properties.AssumeRolePolicyDocument.Statement;
assert.deepStrictEqual(trust.map((s: any) => s.Principal.AWS), [ROLE], 'only the instance role assumes it');
assert.strictEqual(roles[0].Properties.MaxSessionDuration, 3600);

// Collect the actions of every policy: AgentRole's are Bedrock invoke only, the instance role's are AssumeRole only.
const policies = Object.values(t.findResources('AWS::IAM::Policy')) as any[];
const byTarget = (pred: (p: any) => boolean) =>
  policies.filter(pred).flatMap((p) => p.Properties.PolicyDocument.Statement)
    .flatMap((s: any) => ([] as string[]).concat(s.Action));
const agentActions = byTarget((p) => JSON.stringify(p.Properties.Roles).includes('AgentRole'));
assert.deepStrictEqual([...new Set(agentActions)].sort(), [
  'aws-marketplace:Subscribe', 'aws-marketplace:Unsubscribe', 'aws-marketplace:ViewSubscriptions',
  'bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream',
]);
// Marketplace permissions only when going through a Bedrock call — so the sandbox cannot call the Marketplace API directly.
for (const st of policies.flatMap((p) => p.Properties.PolicyDocument.Statement)
  .filter((x: any) => JSON.stringify(x.Action).includes('aws-marketplace'))) {
  assert.deepStrictEqual(st.Condition, { StringEquals: { 'aws:CalledViaLast': 'bedrock.amazonaws.com' } },
    'marketplace actions must be limited to calls made through Bedrock');
}
const instanceActions = byTarget((p) =>
  JSON.stringify(p.Properties.Roles).includes('AipdsHostingStack-InstanceRole3CCE2F1D-QYiZURiI2lpO'));
assert.deepStrictEqual(
  [...new Set(instanceActions)].filter((a) => !a.startsWith('ssm:') && !a.startsWith('aws-marketplace:')),
  ['sts:AssumeRole'], 'the instance role gets AssumeRole on AgentRole');
// The instance role also calls Bedrock directly — environments that do not deploy HostingStack get it only here.
assert.ok(instanceActions.includes('aws-marketplace:Subscribe'),
  'the instance role can complete the first-call Marketplace subscription');
assert.ok(instanceActions.includes('ssm:GetParameter'),
  'the instance role reads the AgentRole ARN parameter at boot');

// Values a new instance reads at boot (lib/instance-params.ts, scripts/aipds-harden boot).
t.hasResourceProperties('AWS::SSM::Parameter', {
  Name: INSTANCE_PARAMS.agentRoleArn,
  Value: { 'Fn::GetAtt': [Match.stringLikeRegexp('^AgentRole'), 'Arn'] },
});

t.hasResourceProperties('AWS::IAM::Policy', {
  PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({
    Action: Match.arrayWith(['bedrock:InvokeModel']),
    Resource: Match.arrayWith([
      'arn:aws:bedrock:*:123456789012:inference-profile/global.anthropic.claude-*',
      'arn:aws:bedrock:*::foundation-model/anthropic.claude-*',
    ]),
  })]) },
});
t.hasOutput('AgentRoleArn', {});
console.log('OK  agent creds stack: Bedrock-only AgentRole, assumable by the instance role only');
