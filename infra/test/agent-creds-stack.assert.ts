import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { AipdsAgentCredsStack } from '../lib/aipds-agent-creds-stack';

// AgentRole의 계약(lib/aipds-agent-creds-stack.ts 머리말):
//   - 인스턴스 롤만 AssumeRole한다. 그 권한은 인스턴스 롤에 붙는 별도 정책이다.
//   - Bedrock invoke 말고는 아무것도 없다(S3·Cognito·Secrets Manager 없음).
//   - 인스턴스 쪽 리소스를 만들지 않는다.

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

// 모든 정책의 액션을 모은다: AgentRole의 것은 Bedrock invoke뿐, 인스턴스 롤의 것은 AssumeRole뿐.
const policies = Object.values(t.findResources('AWS::IAM::Policy')) as any[];
const byTarget = (pred: (p: any) => boolean) =>
  policies.filter(pred).flatMap((p) => p.Properties.PolicyDocument.Statement)
    .flatMap((s: any) => [].concat(s.Action));
const agentActions = byTarget((p) => JSON.stringify(p.Properties.Roles).includes('AgentRole'));
assert.deepStrictEqual([...new Set(agentActions)].sort(),
  ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream']);
const instanceActions = byTarget((p) =>
  JSON.stringify(p.Properties.Roles).includes('AipdsHostingStack-InstanceRole3CCE2F1D-QYiZURiI2lpO'));
assert.deepStrictEqual(instanceActions, ['sts:AssumeRole'], 'the instance role gets AssumeRole on AgentRole');

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
