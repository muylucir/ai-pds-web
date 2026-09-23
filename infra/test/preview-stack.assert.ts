import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { AipdsPreviewStack, PREVIEW_PATH_PATTERN } from '../lib/aipds-preview-stack';

// 프리뷰 오리진의 계약(lib/aipds-preview-stack.ts 머리말):
//   - 프로토타입 경로만 오리진으로 가고 나머지는 404다.
//   - 두 비밀 헤더를 모두 붙인다 — X-Origin-Verify는 nginx가, X-Preview-Verify는 백엔드가 본다.
//   - 인스턴스 롤이 프리뷰 시크릿을 읽을 수 있다(HostingStack을 고치지 않고).
//   - EC2·VPC·IAM 롤 같은 인스턴스 쪽 리소스를 만들지 않는다 — 참조만 한다.

const ENV = { account: '123456789012', region: 'ap-northeast-2' };
const ROLE = 'arn:aws:iam::123456789012:role/AipdsHostingStack-InstanceRole';
const SECRET = 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:OriginVerifyHeader-AbCdEf';

const app = new cdk.App();
const stack = new AipdsPreviewStack(app, 'Preview', {
  env: ENV,
  originDnsName: 'ec2-3-39-42-222.ap-northeast-2.compute.amazonaws.com',
  originVerifySecretArn: SECRET,
  instanceRoleArn: ROLE,
});
const t = Template.fromStack(stack);

// 인스턴스 쪽 리소스를 만들지 않는다.
for (const type of ['AWS::EC2::Instance', 'AWS::EC2::VPC', 'AWS::IAM::Role', 'AWS::EC2::EIP']) {
  assert.deepStrictEqual(t.findResources(type), {}, `preview stack must not create ${type}`);
}

const dist = Object.values(t.findResources('AWS::CloudFront::Distribution'))[0] as any;
const cfg = dist.Properties.DistributionConfig;

// 기본 동작은 404 함수, 프로토타입 경로만 오리진.
assert.strictEqual(cfg.DefaultCacheBehavior.FunctionAssociations[0].EventType, 'viewer-request');
const proto = cfg.CacheBehaviors.find((b: any) => b.PathPattern === PREVIEW_PATH_PATTERN);
assert.ok(proto, `a behavior for ${PREVIEW_PATH_PATTERN}`);
assert.strictEqual(proto.FunctionAssociations, undefined, 'the prototype path is not 404');
assert.deepStrictEqual(proto.AllowedMethods.length, 7, 'prototypes take form posts');
assert.strictEqual(cfg.CacheBehaviors.length, 1, 'nothing else reaches the origin');

// 두 비밀 헤더.
const headers = JSON.stringify(cfg.Origins[0].OriginCustomHeaders);
assert.ok(headers.includes('X-Origin-Verify'), 'nginx checks X-Origin-Verify');
assert.ok(headers.includes('X-Preview-Verify'), 'the backend checks X-Preview-Verify');
assert.ok(headers.includes(SECRET), 'X-Origin-Verify comes from the HostingStack secret');
assert.ok(!headers.match(/"Value":"[A-Za-z0-9]{32}"/), 'no plaintext secret in the template');

// 인스턴스 롤이 프리뷰 시크릿을 읽는다.
t.hasResourceProperties('AWS::SecretsManager::ResourcePolicy', {
  ResourcePolicy: {
    Statement: Match.arrayWith([Match.objectLike({
      Action: 'secretsmanager:GetSecretValue',
      Principal: { AWS: ROLE },
    })]),
  },
});

t.hasOutput('PreviewOrigin', {});
t.hasOutput('PreviewSecretArn', {});
console.log('OK  preview stack: proto-only behaviors, both verify headers, role can read its secret, no instance resources');
