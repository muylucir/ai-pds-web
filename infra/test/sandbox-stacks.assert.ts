import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AipdsDrillStack } from '../lib/aipds-drill-stack';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import { AipdsHostingStack } from '../lib/aipds-hosting-stack';
import { addSandboxStacks, pinnedFromEnv } from '../lib/sandbox-stacks';

// The two modes of lib/sandbox-stacks.ts.
//   - New environment: both stacks receive values from HostingStack by reference → a single `cdk deploy --all` finishes it.
//   - Running environment (pinned): both stacks **do not depend on HostingStack.** If they did,
//     `cdk deploy AipdsAgentCredsStack` would also deploy HostingStack, and that deployment can replace the EC2 instance.

const ENV = { account: '123456789012', region: 'ap-northeast-2' };
const PINNED = {
  instanceRoleArn: 'arn:aws:iam::123456789012:role/AipdsHostingStack-InstanceRole-X',
  originDnsName: 'ec2-1-2-3-4.ap-northeast-2.compute.amazonaws.com',
  originVerifySecretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:hdr-AbCdEf',
  artifactsBucket: 'aipdsdrillstack-artifacts-x',
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
  const stacks = addSandboxStacks(app, ENV, drill, hosting, pinned);
  // Dependencies created by cross-stack references are resolved at synth.
  const asm = app.synth();
  const dependsOn = (stack: cdk.Stack, on: cdk.Stack) =>
    asm.getStackArtifact(stack.artifactId).dependencies.some((d) => d.id === on.artifactId);
  return { ...stacks, drill, dependsOnHosting: (st: cdk.Stack) => dependsOn(st, hosting),
           dependsOnDrill: (st: cdk.Stack) => dependsOn(st, drill) };
}

{
  const { preview, agentCreds, uploadCors, dependsOnHosting, dependsOnDrill } = build(PINNED);
  for (const stack of [preview, agentCreds, uploadCors]) {
    assert.ok(!dependsOnDrill(stack),
      `${stack.stackName} must not depend on DrillStack when pinned — deploying it would redeploy `
      + 'the bucket stack (and its CORS) too');
    assert.ok(!dependsOnHosting(stack),
      `${stack.stackName} must not depend on HostingStack when pinned — deploying it would deploy `
      + 'HostingStack too');
    assert.ok(!JSON.stringify(Template.fromStack(stack).toJSON()).includes('Fn::ImportValue'),
      `${stack.stackName} must not import from HostingStack when pinned`);
  }
}

{
  const { preview, agentCreds, uploadCors, dependsOnHosting } = build();
  for (const stack of [preview, agentCreds, uploadCors]) {
    assert.ok(dependsOnHosting(stack),
      `${stack.stackName} takes HostingStack's values in a new environment — deploy --all orders it after`);
  }
  // AgentRole's trusted principal is the actual instance role (a value passed by reference).
  const role = Object.values(Template.fromStack(agentCreds).findResources('AWS::IAM::Role'))[0] as any;
  assert.ok(JSON.stringify(role.Properties.AssumeRolePolicyDocument).includes('Fn::ImportValue'),
    'the AgentRole trusts the HostingStack instance role');
}

assert.strictEqual(pinnedFromEnv({}), undefined, 'no env: a new environment');
assert.deepStrictEqual(pinnedFromEnv({
  AIPDS_INSTANCE_ROLE_ARN: PINNED.instanceRoleArn,
  AIPDS_PREVIEW_ORIGIN_DNS: PINNED.originDnsName,
  AIPDS_ORIGIN_VERIFY_SECRET_ARN: PINNED.originVerifySecretArn,
  AIPDS_ARTIFACTS_BUCKET: PINNED.artifactsBucket,
}), PINNED);
// Pinning only some values ties the remaining stacks to HostingStack — their deployment drags HostingStack along.
assert.throws(() => pinnedFromEnv({ AIPDS_INSTANCE_ROLE_ARN: PINNED.instanceRoleArn }), /all or none/);

// Bucket CORS permission: only CORS read/write on that bucket, granted to the instance role.
{
  const { uploadCors } = build(PINNED);
  const t = Template.fromStack(uploadCors);
  assert.deepStrictEqual(t.findResources('AWS::IAM::Role'), {}, 'no new role');
  const statements = Object.values(t.findResources('AWS::IAM::Policy'))
    .flatMap((p: any) => p.Properties.PolicyDocument.Statement);
  assert.deepStrictEqual(statements.map((st: any) => [[].concat(st.Action).sort(), st.Resource]),
    [[['s3:GetBucketCORS', 's3:PutBucketCORS'], `arn:aws:s3:::${PINNED.artifactsBucket}`]]);
}

console.log('OK  sandbox stacks: new env references HostingStack/DrillStack, pinned env depends on neither; upload CORS grant is bucket-CORS only');
