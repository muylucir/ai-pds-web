import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { AipdsPreviewStack, PREVIEW_PATH_PATTERN } from '../lib/aipds-preview-stack';
import { INSTANCE_PARAMS } from '../lib/instance-params';

// The preview origin's contract (header of lib/aipds-preview-stack.ts):
//   - Only prototype paths go to the origin; everything else is a 404.
//   - Both secret headers are attached — nginx checks X-Origin-Verify, the backend checks X-Preview-Verify.
//   - It reaches the instance through HostingStack's VPC origin — it does not create one of its own.
//   - The instance role can read the preview secret (without modifying HostingStack).
//   - It does not create instance-side resources such as EC2, VPC, or IAM roles — it only references them.

const ENV = { account: '123456789012', region: 'ap-northeast-2' };
const ROLE = 'arn:aws:iam::123456789012:role/AipdsHostingStack-InstanceRole';
const SECRET = 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:OriginVerifyHeader-AbCdEf';

const app = new cdk.App();
const stack = new AipdsPreviewStack(app, 'Preview', {
  env: ENV,
  vpcOriginId: 'vo_test0000',
  originPrivateDns: 'ip-10-0-2-10.ap-northeast-2.compute.internal',
  originVerifySecretArn: SECRET,
  instanceRoleArn: ROLE,
});
const t = Template.fromStack(stack);

// Does not create instance-side resources.
for (const type of ['AWS::EC2::Instance', 'AWS::EC2::VPC', 'AWS::IAM::Role', 'AWS::EC2::EIP',
                    'AWS::CloudFront::VpcOrigin']) {
  assert.deepStrictEqual(t.findResources(type), {}, `preview stack must not create ${type}`);
}

const dist = Object.values(t.findResources('AWS::CloudFront::Distribution'))[0] as any;
const cfg = dist.Properties.DistributionConfig;

// The default behavior is the 404 function; only prototype paths go to the origin.
assert.strictEqual(cfg.DefaultCacheBehavior.FunctionAssociations[0].EventType, 'viewer-request');
const proto = cfg.CacheBehaviors.find((b: any) => b.PathPattern === PREVIEW_PATH_PATTERN);
assert.ok(proto, `a behavior for ${PREVIEW_PATH_PATTERN}`);
assert.strictEqual(proto.FunctionAssociations, undefined, 'the prototype path is not 404');
assert.deepStrictEqual(proto.AllowedMethods.length, 7, 'prototypes take form posts');
assert.strictEqual(cfg.CacheBehaviors.length, 1, 'nothing else reaches the origin');

// The origin is HostingStack's VPC origin, addressed by the instance's private DNS name.
assert.strictEqual(cfg.Origins.length, 1);
assert.deepStrictEqual(cfg.Origins[0].VpcOriginConfig.VpcOriginId, 'vo_test0000');
assert.strictEqual(cfg.Origins[0].DomainName, 'ip-10-0-2-10.ap-northeast-2.compute.internal');
assert.strictEqual(cfg.Origins[0].CustomOriginConfig, undefined, 'no public custom origin');

// The two secret headers.
const headers = JSON.stringify(cfg.Origins[0].OriginCustomHeaders);
assert.ok(headers.includes('X-Origin-Verify'), 'nginx checks X-Origin-Verify');
assert.ok(headers.includes('X-Preview-Verify'), 'the backend checks X-Preview-Verify');
assert.ok(headers.includes(SECRET), 'X-Origin-Verify comes from the HostingStack secret');
assert.ok(!headers.match(/"Value":"[A-Za-z0-9]{32}"/), 'no plaintext secret in the template');

// The instance role reads the preview secret.
t.hasResourceProperties('AWS::SecretsManager::ResourcePolicy', {
  ResourcePolicy: {
    Statement: Match.arrayWith([Match.objectLike({
      Action: 'secretsmanager:GetSecretValue',
      Principal: { AWS: ROLE },
    })]),
  },
});

// The two values a new instance reads at boot (lib/instance-params.ts, scripts/aipds-harden boot).
const params = Object.values(t.findResources('AWS::SSM::Parameter')) as any[];
assert.deepStrictEqual(params.map((p) => p.Properties.Name).sort(),
  [INSTANCE_PARAMS.previewOrigin, INSTANCE_PARAMS.previewSecretArn].sort());
const readActions = Object.values(t.findResources('AWS::IAM::Policy'))
  .filter((p: any) => JSON.stringify(p.Properties.Roles).includes('AipdsHostingStack-InstanceRole'))
  .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
  .flatMap((st: any) => ([] as string[]).concat(st.Action));
assert.ok(readActions.includes('ssm:GetParameter'), 'the instance role reads them at boot');
assert.ok(readActions.every((a: string) => a.startsWith('ssm:')), 'and nothing else');

t.hasOutput('PreviewOrigin', {});
t.hasOutput('PreviewSecretArn', {});
console.log('OK  preview stack: proto-only behaviors, shared VPC origin, both verify headers, role can read its secret, boot parameters, no instance resources');
