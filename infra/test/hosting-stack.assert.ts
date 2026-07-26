import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';
import { PathfinderAuthStack } from '../lib/pathfinder-auth-stack';

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
  const auth = new PathfinderAuthStack(app, 'Auth2', { env: ENV });
  const stack = new PathfinderHostingStack(app, 'Hosting', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-test0000',   // 주입 → fromLookup 우회(크리덴셜 불필요)
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
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
  // x86_64 인스턴스 1대, IMDSv2 강제(HttpTokens required). Graviton은 쓰지
  // 않는다 — SDK 번들 바이너리가 x86-64 ELF이고, 프로토타입이 설치하는
  // 네이티브 npm 모듈도 x86_64 prebuilt를 받는다.
  t.hasResourceProperties('AWS::EC2::Instance', {
    InstanceType: 'm7i.2xlarge',
  });
  t.hasResourceProperties('AWS::EC2::LaunchTemplate', {
    LaunchTemplateData: Match.objectLike({
      MetadataOptions: Match.objectLike({ HttpTokens: 'required' }),
    }),
  });
  // 빌드가 이 박스로 들어오면서 프로토타입당 node_modules가 상주한다 — 20GB로는
  // 부족하다.
  t.hasResourceProperties('AWS::EC2::Instance', {
    BlockDeviceMappings: Match.arrayWith([
      Match.objectLike({ Ebs: Match.objectLike({ VolumeSize: 100 }) }),
    ]),
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
  // lambda-microvms 제어 권한은 VM 계층과 함께 사라졌다.
  if (allActions.includes('lambda-microvms')) {
    throw new Error('hosting: instance role still carries lambda-microvms permissions');
  }
  console.log('OK  hosting: EC2 x86_64 + 100GB EBS + EIP + instance role (bedrock/secret/ssm, no microvm)');
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

// Custom::AWS의 Create/Update 필드는 SDK 호출을 기술하는 JSON을 Fn::Join으로
// 조립한 값이다(참조 부분만 { Ref: ... } / { Fn::GetAtt: ... } 등) — 문자열
// 조각을 이어붙이고 참조는 자리표시자로 바꿔 JSON.parse하면 실제
// service/action/parameters를 얻는다. 이래야 "필드가 다 있는지"뿐 아니라
// "값이 맞는지"(예: AllowedOAuthFlows가 정확히 ['code']인지, ['implicit']이
// 섞여 있진 않은지)까지 검증할 수 있다 — 문자열 포함 검사(bodies.includes(...))는
// 값이 달라도 필드 이름만 있으면 통과해버린다.
function parseSdkPayload(field: any): { service: string; action: string; parameters: any } {
  const parts = field['Fn::Join'][1];
  const joined = parts.map((p: any) => (typeof p === 'string' ? p : '__REF__')).join('');
  return JSON.parse(joined);
}

// --- 콜백 URL 주입 (순환 의존 해소) ---
{
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill3', { env: ENV });
  const auth = new PathfinderAuthStack(app, 'Auth3', { env: ENV });
  const hosting = new PathfinderHostingStack(app, 'Hosting3', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-1234',
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);
  const customResources = t.findResources('Custom::AWS');
  const resourceList = Object.values(customResources);
  const target = resourceList.find((r: any) => {
    try {
      return parseSdkPayload(r.Properties.Create).action === 'updateUserPoolClient';
    } catch {
      return false;
    }
  }) as any;
  assert.ok(target, 'hosting must register the CloudFront callback URL with the app client');

  // onCreate와 onUpdate가 둘 다 있어야 한다 — Update가 없으면 재배포 시 콜백이
  // 갱신되지 않아 도메인이 바뀌어도 낡은 채로 남는다. 두 objects가 같은
  // 참조(updateClientCall 하나를 공유)이므로 Create만 있어도 Create 쪽 문자열
  // 검사는 전부 통과해버린다 — 그래서 Update 필드의 '존재' 자체를 별도로 확인한다.
  assert.ok(target.Properties.Update, 'onUpdate must be set — otherwise a redeploy never refreshes stale callbacks');

  const createPayload = parseSdkPayload(target.Properties.Create);
  const updatePayload = parseSdkPayload(target.Properties.Update);
  assert.strictEqual(updatePayload.action, 'updateUserPoolClient', 'onUpdate must call the same SDK action as onCreate');

  const params = createPayload.parameters;

  // PUT 시맨틱이므로 전체 설정을 다시 써야 한다 — 필드가 있는지뿐 아니라 값도
  // 확인한다. ['implicit']이 섞여 들어가도 'AllowedOAuthFlows' 존재 여부만
  // 보는 검사로는 못 잡는다.
  assert.deepStrictEqual(params.AllowedOAuthFlows, ['code'],
    'UpdateUserPoolClient has PUT semantics — must resend the exact flow, not merely "a" flow');
  assert.strictEqual(params.AllowedOAuthFlowsUserPoolClient, true);
  assert.deepStrictEqual(
    [...params.AllowedOAuthScopes].sort(), ['email', 'openid', 'profile'],
    'OAuth scopes must be resent',
  );
  assert.deepStrictEqual(params.SupportedIdentityProviders, ['COGNITO']);
  assert.strictEqual(params.PreventUserExistenceErrors, 'ENABLED');
  assert.strictEqual(params.EnableTokenRevocation, true);
  // 토큰 유효기간 + 리프레시 인증 플로우 — 처음 구현에서 빠졌던 필드들.
  // AuthStack의 클라이언트 정의(1h/1h/30d, ALLOW_REFRESH_TOKEN_AUTH)와
  // 정확히 같은 값이어야 한다: 하나라도 빠지면 재배포마다 그 필드가
  // Cognito 기본값으로 조용히 리셋된다.
  assert.strictEqual(params.AccessTokenValidity, 60);
  assert.strictEqual(params.IdTokenValidity, 60);
  assert.strictEqual(params.RefreshTokenValidity, 60 * 24 * 30);
  assert.deepStrictEqual(params.TokenValidityUnits, {
    AccessToken: 'minutes', IdToken: 'minutes', RefreshToken: 'minutes',
  });
  assert.deepStrictEqual(params.ExplicitAuthFlows, ['ALLOW_REFRESH_TOKEN_AUTH'],
    'the /api proxy 401-refresh path depends on this grant — dropping it silently breaks session renewal on redeploy');

  // localhost 콜백도 유지돼야 로컬 개발이 깨지지 않는다.
  assert.ok(params.CallbackURLs.includes('http://localhost:3000/api/auth/callback'),
    'the localhost callback must survive the update');
  assert.ok(params.LogoutURLs.includes('http://localhost:3000/login'),
    'the localhost logout URL must survive the update');

  // 인스턴스 롤이 클라이언트 시크릿을 읽을 수 있어야 한다(부팅 시 조회).
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({ Action: 'cognito-idp:DescribeUserPoolClient' }),
      ]),
    },
  });
  console.log('OK  hosting stack: callback URL injection + full client config resend (incl. token validity/refresh flow) + onUpdate present + secret read permission');
}
