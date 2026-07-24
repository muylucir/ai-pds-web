import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

function testDrillUnchanged() {
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill', { env: ENV });
  const t = Template.fromStack(drill);

  // Bedrock invoke 문 존재.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream']),
        }),
      ]),
    },
  });
  // S3 객체 Get/Put/Delete 문 존재.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['s3:GetObject', 's3:PutObject', 's3:DeleteObject']),
        }),
      ]),
    },
  });
  // S3 ListBucket 문 + projects/*·sessions/* prefix 조건 존재.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          // 단일 액션은 CFN 합성 시 배열이 아닌 스칼라 문자열로 축약됨.
          Action: 's3:ListBucket',
          Condition: {
            StringLike: {
              's3:prefix': Match.arrayWith(['projects/*', 'sessions/*']),
            },
          },
        }),
      ]),
    },
  });
  // 버킷 1개 노출.
  assert.ok(drill.artifactsBucket, 'artifactsBucket must be exposed');
  t.resourceCountIs('AWS::S3::Bucket', 1);
  console.log('OK  drill stack: bedrock + s3 object + s3 listBucket(prefix) policy statements + bucket exposed');
}

testDrillUnchanged();

function makeHosting() {
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill2', { env: ENV });
  const stack = new PathfinderHostingStack(app, 'Hosting', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-test0000',   // 주입 → fromLookup 우회(크리덴셜 불필요)
  });
  return Template.fromStack(stack);
}

function testNetworkAndSecret() {
  const t = makeHosting();
  // 80 인그레스가 프리픽스 리스트 소스만 사용, CIDR 오픈 없음.
  t.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
    IpProtocol: 'tcp', FromPort: 80, ToPort: 80, SourcePrefixListId: 'pl-test0000',
  });
  // 22(SSH) 인그레스 전무.
  const ingresses = t.findResources('AWS::EC2::SecurityGroupIngress');
  for (const [, r] of Object.entries(ingresses)) {
    assert.notStrictEqual((r as any).Properties.FromPort, 22, 'no SSH ingress allowed');
  }
  // 0.0.0.0/0 인그레스 없음(별도 인라인 ingress도 없어야).
  const sgs = t.findResources('AWS::EC2::SecurityGroup');
  for (const [, r] of Object.entries(sgs)) {
    const inline = (r as any).Properties.SecurityGroupIngress ?? [];
    for (const rule of inline) {
      assert.notStrictEqual(rule.CidrIp, '0.0.0.0/0', 'no open CIDR ingress');
    }
  }
  // NAT 게이트웨이 0.
  t.resourceCountIs('AWS::EC2::NatGateway', 0);
  // 비밀 헤더 시크릿 존재(구두점 제외 생성).
  t.hasResourceProperties('AWS::SecretsManager::Secret', {
    GenerateSecretString: Match.objectLike({ ExcludePunctuation: true }),
  });
  console.log('OK  hosting: SG prefix-list only, no SSH, secret present');
}

testNetworkAndSecret();

function testComputeAndRole() {
  const t = makeHosting();
  // arm64 인스턴스 1대, IMDSv2 강제(HttpTokens required).
  t.hasResourceProperties('AWS::EC2::Instance', {
    InstanceType: 't4g.medium',
  });
  t.hasResourceProperties('AWS::EC2::LaunchTemplate', {
    LaunchTemplateData: Match.objectLike({
      MetadataOptions: Match.objectLike({ HttpTokens: 'required' }),
    }),
  });
  // EIP 존재 + 연결.
  t.resourceCountIs('AWS::EC2::EIP', 1);
  t.resourceCountIs('AWS::EC2::EIPAssociation', 1);
  // 인스턴스 롤: Bedrock + 시크릿 읽기 + SSM 관리형 정책.
  t.hasResourceProperties('AWS::IAM::Role', {
    ManagedPolicyArns: Match.arrayWith([
      Match.objectLike({
        'Fn::Join': Match.arrayWith([
          Match.arrayWith([Match.stringLikeRegexp('AmazonSSMManagedInstanceCore')]),
        ]),
      }),
    ]),
  });
  const policies = t.findResources('AWS::IAM::Policy');
  const allActions = JSON.stringify(policies);
  assert.match(allActions, /secretsmanager:GetSecretValue/, 'instance role reads header secret');
  assert.match(allActions, /bedrock:InvokeModel/, 'instance role invokes bedrock');
  console.log('OK  hosting: EC2 arm64 + EIP + instance role (bedrock/secret/ssm)');
}

testComputeAndRole();

function testCloudFront() {
  const t = makeHosting();
  t.hasResourceProperties('AWS::CloudFront::Distribution', {
    DistributionConfig: Match.objectLike({
      Origins: Match.arrayWith([
        Match.objectLike({
          CustomOriginConfig: Match.objectLike({ OriginProtocolPolicy: 'http-only' }),
          OriginCustomHeaders: Match.arrayWith([
            Match.objectLike({ HeaderName: 'X-Origin-Verify' }),
          ]),
        }),
      ]),
      DefaultCacheBehavior: Match.objectLike({
        ViewerProtocolPolicy: 'redirect-to-https',
      }),
    }),
  });
  // /_next/static/* 캐시 비헤이비어 존재.
  const dists = t.findResources('AWS::CloudFront::Distribution');
  const cfg = Object.values(dists)[0] as any;
  const behaviors = cfg.Properties.DistributionConfig.CacheBehaviors ?? [];
  assert.ok(
    behaviors.some((b: any) => b.PathPattern === '/_next/static/*'),
    'static cache behavior present',
  );
  console.log('OK  hosting: CloudFront http-only origin + X-Origin-Verify header + https redirect');
}

testCloudFront();
