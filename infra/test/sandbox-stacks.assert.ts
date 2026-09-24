import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AipdsDrillStack } from '../lib/aipds-drill-stack';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import { AipdsHostingStack } from '../lib/aipds-hosting-stack';
import { addSandboxStacks, pinnedFromEnv } from '../lib/sandbox-stacks';

// lib/sandbox-stacks.ts의 두 모드.
//   - 새 환경: 두 스택이 HostingStack에서 값을 참조로 받는다 → `cdk deploy --all` 한 번으로 끝난다.
//   - 떠 있는 환경(pinned): 두 스택이 HostingStack에 **의존하지 않는다.** 의존하면
//     `cdk deploy AipdsAgentCredsStack`이 HostingStack까지 배포하고, 그 배포는 EC2를 교체할 수 있다.

const ENV = { account: '123456789012', region: 'ap-northeast-2' };
const PINNED = {
  instanceRoleArn: 'arn:aws:iam::123456789012:role/AipdsHostingStack-InstanceRole-X',
  originDnsName: 'ec2-1-2-3-4.ap-northeast-2.compute.amazonaws.com',
  originVerifySecretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:hdr-AbCdEf',
};

function build(pinned?: typeof PINNED) {
  const app = new cdk.App();
  const drill = new AipdsDrillStack(app, 'AipdsDrillStack', { env: ENV });
  const auth = new AipdsAuthStack(app, 'AipdsAuthStack', { env: ENV });
  const hosting = new AipdsHostingStack(app, 'AipdsHostingStack', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-test0000',
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const stacks = addSandboxStacks(app, ENV, hosting, pinned);
  // 스택 간 참조가 만드는 의존성은 synth에서 해석된다.
  const asm = app.synth();
  const dependsOnHosting = (stack: cdk.Stack) =>
    asm.getStackArtifact(stack.artifactId).dependencies.some((d) => d.id === hosting.artifactId);
  return { ...stacks, dependsOnHosting };
}

{
  const { preview, agentCreds, dependsOnHosting } = build(PINNED);
  for (const stack of [preview, agentCreds]) {
    assert.ok(!dependsOnHosting(stack),
      `${stack.stackName} must not depend on HostingStack when pinned — deploying it would deploy `
      + 'HostingStack too');
    assert.ok(!JSON.stringify(Template.fromStack(stack).toJSON()).includes('Fn::ImportValue'),
      `${stack.stackName} must not import from HostingStack when pinned`);
  }
}

{
  const { preview, agentCreds, dependsOnHosting } = build();
  for (const stack of [preview, agentCreds]) {
    assert.ok(dependsOnHosting(stack),
      `${stack.stackName} takes HostingStack's values in a new environment — deploy --all orders it after`);
  }
  // AgentRole의 신뢰 주체가 실제 인스턴스 롤이다(참조로 온 값).
  const role = Object.values(Template.fromStack(agentCreds).findResources('AWS::IAM::Role'))[0] as any;
  assert.ok(JSON.stringify(role.Properties.AssumeRolePolicyDocument).includes('Fn::ImportValue'),
    'the AgentRole trusts the HostingStack instance role');
}

assert.strictEqual(pinnedFromEnv({}), undefined, 'no env: a new environment');
assert.deepStrictEqual(pinnedFromEnv({
  AIPDS_INSTANCE_ROLE_ARN: PINNED.instanceRoleArn,
  AIPDS_PREVIEW_ORIGIN_DNS: PINNED.originDnsName,
  AIPDS_ORIGIN_VERIFY_SECRET_ARN: PINNED.originVerifySecretArn,
}), PINNED);
// 롤만 고정하면 프리뷰 스택만 HostingStack에 묶인다 — 그 배포가 HostingStack을 끌고 온다.
assert.throws(() => pinnedFromEnv({ AIPDS_INSTANCE_ROLE_ARN: PINNED.instanceRoleArn }), /all three/);

console.log('OK  sandbox stacks: new env references HostingStack, pinned env never depends on it');
