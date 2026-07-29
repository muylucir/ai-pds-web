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
  // Opus 5·Sonnet 5까지 invoke 가능해야 한다. ANTHROPIC_MODEL(기본 Opus 4.8)만
  // 허용하면 모델을 바꿔보려는 순간 AccessDenied가 나고, 그 실패는 첫 대화 턴에
  // 가서야 드러난다 — env 한 줄로 전환할 수 있게 권한을 미리 넓혀 둔다.
  for (const m of ['claude-opus-5', 'claude-sonnet-5', 'claude-opus-4-8']) {
    assert.match(allActions, new RegExp(`inference-profile/global\\.anthropic\\.${m}`),
      `instance role can invoke ${m} inference profile`);
    assert.match(allActions, new RegExp(`foundation-model/anthropic\\.${m}`),
      `instance role can invoke ${m} foundation model`);
  }
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

  // --- 드리프트 감지: AuthStack의 client 속성 하나하나를 손으로 대조하는 건
  // 반복 가능한 방어가 아니다(리뷰어가 CDK 소스를 읽어야 ClientName 누락을
  // 찾을 수 있었다). 그래서 실제로 AuthStack을 합성해 그 client 리소스가
  // 갖는 CFN 속성 키 전부를 가져오고, 재전송 parameters가 그 키를 다 갖고
  // 있는지 기계적으로 대조한다 — 다음에 addClient()에 필드가 추가되고
  // 재전송이 안 고쳐지면, 이 단정이 그 필드 이름을 대며 즉시 실패한다.
  const authTemplate = Template.fromStack(auth);
  const authClients = authTemplate.findResources('AWS::Cognito::UserPoolClient');
  const authClientProps = (Object.values(authClients)[0] as any).Properties;

  // 정당하게 다른 필드들:
  //  - CallbackURLs/LogoutURLs: 재전송이 CloudFront 도메인을 의도적으로
  //    덧붙이므로 AuthStack의 localhost-only 값과 다른 게 정상이다(위에서
  //    이미 localhost 콜백 생존을 따로 확인했다).
  //  - UserPoolId/ClientId: 이 리소스를 "어떤 클라이언트"로 지정하는
  //    주소값이라 설정이 아니다 — AuthStack 쪽 UserPoolId는 { Ref: ... }
  //    토큰이고 재전송 쪽은 실제 문자열이라 형태부터 다르다.
  //  - GenerateSecret: CreateUserPoolClient에만 있는 필드다(시크릿 유무는
  //    생성 후 못 바꾼다) — UpdateUserPoolClient 요청 문법에 이 필드가
  //    없으므로 여기 보내면 오히려 API가 거부한다.
  const legitimatelyDifferent = new Set([
    'CallbackURLs', 'LogoutURLs', 'UserPoolId', 'ClientId', 'GenerateSecret',
  ]);
  for (const key of Object.keys(authClientProps)) {
    if (legitimatelyDifferent.has(key)) continue;
    assert.ok(
      Object.prototype.hasOwnProperty.call(params, key),
      `AuthStack's client sets '${key}' but the UpdateUserPoolClient resend does not — ` +
      `PUT semantics will reset it to a Cognito default on the next deploy`,
    );
  }

  // 인스턴스 롤이 클라이언트 시크릿을 읽을 수 있어야 한다(부팅 시 조회).
  // 액션은 /admin/users용 Admin* 권한과 같은 문에 배열로 들어 있다 —
  // 전체 목록 고정은 아래 "instance role has every Cognito Admin action"에서.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['cognito-idp:DescribeUserPoolClient']),
        }),
      ]),
    },
  });
  console.log('OK  hosting stack: callback URL injection + full client config resend (incl. token validity/refresh flow) + onUpdate present + secret read permission');
}

// --- 비ASCII 문자가 리소스 속성에 새지 않는지 ---
// 실측 배포 실패: EC2가 SecurityGroup의 GroupDescription에서 비ASCII를 거부한다
// ("Character sets beyond ASCII are not supported"). 한국어 주석 습관이 그대로
// description/comment 문자열에 들어가 em dash(—)가 섞였다. 합성은 통과하고
// CloudFormation이 API를 호출하는 시점에 죽는다.
//
// Cognito 그룹 설명(한국어)은 실제로 배포에 성공하므로 전면 금지는 과하다 —
// ASCII를 강제하는 서비스의 속성만 검사한다. 주석은 대상이 아니다(템플릿에
// 나가지 않는다).
{
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill4', { env: ENV });
  const auth = new PathfinderAuthStack(app, 'Auth4', { env: ENV });
  const hosting = new PathfinderHostingStack(app, 'Hosting4', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);

  const nonAscii = /[^\x00-\x7F]/;

  // EC2 SecurityGroup: GroupDescription + 인바운드/아웃바운드 규칙 설명.
  for (const [id, sg] of Object.entries(t.findResources('AWS::EC2::SecurityGroup'))) {
    const p: any = (sg as any).Properties ?? {};
    for (const field of ['GroupDescription', 'GroupName']) {
      const v = p[field];
      if (typeof v === 'string') {
        assert.ok(!nonAscii.test(v),
          `${id}.${field} must be ASCII-only (EC2 rejects non-ASCII): ${JSON.stringify(v)}`);
      }
    }
    for (const key of ['SecurityGroupIngress', 'SecurityGroupEgress']) {
      for (const rule of (p[key] ?? []) as any[]) {
        if (typeof rule?.Description === 'string') {
          assert.ok(!nonAscii.test(rule.Description),
            `${id}.${key}[].Description must be ASCII-only: ${JSON.stringify(rule.Description)}`);
        }
      }
    }
  }
  // 별도 리소스로 떨어지는 규칙도 같은 제약을 받는다.
  for (const type of ['AWS::EC2::SecurityGroupIngress', 'AWS::EC2::SecurityGroupEgress']) {
    for (const [id, r] of Object.entries(t.findResources(type))) {
      const d = ((r as any).Properties ?? {}).Description;
      if (typeof d === 'string') {
        assert.ok(!nonAscii.test(d), `${id}.Description must be ASCII-only: ${JSON.stringify(d)}`);
      }
    }
  }
  // CloudFront Comment — 콘솔 표시용이고 ASCII 안전이 확인되지 않았다.
  for (const [id, d] of Object.entries(t.findResources('AWS::CloudFront::Distribution'))) {
    const c = ((d as any).Properties ?? {}).DistributionConfig?.Comment;
    if (typeof c === 'string') {
      assert.ok(!nonAscii.test(c), `${id} Comment must be ASCII-only: ${JSON.stringify(c)}`);
    }
  }
  console.log('OK  hosting: ASCII-only SG descriptions + CloudFront comment (EC2 rejects non-ASCII)');
}

// --- /admin/users가 쓰는 Cognito Admin API 권한 ---
// 실측 배포 버그: 로그인은 되는데 /admin/users가 502였다. 백엔드 로그:
// "cognito call failed (AccessDeniedException) -> 502". 인스턴스 롤에는 부팅
// 시 시크릿을 읽는 DescribeUserPoolClient 하나만 있었고, 사용자 관리 API
// 권한이 전혀 없었다.
//
// backend/pathfinder/auth/cognito.py가 실제로 호출하는 액션 전체를 고정한다 —
// 하나라도 빠지면 그 기능만 502가 되고, 화면에서는 원인이 보이지 않는다.
{
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill5', { env: ENV });
  const auth = new PathfinderAuthStack(app, 'Auth5', { env: ENV });
  const hosting = new PathfinderHostingStack(app, 'Hosting5', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);

  // cognito.py의 _call() 호출 전수(10개) + 부팅용 DescribeUserPoolClient.
  const required = [
    'cognito-idp:AdminAddUserToGroup',
    'cognito-idp:AdminCreateUser',
    'cognito-idp:AdminDeleteUser',
    'cognito-idp:AdminDisableUser',
    'cognito-idp:AdminEnableUser',
    'cognito-idp:AdminListGroupsForUser',
    'cognito-idp:AdminRemoveUserFromGroup',
    'cognito-idp:AdminSetUserPassword',
    'cognito-idp:DescribeUserPoolClient',
    'cognito-idp:ListUsers',
    'cognito-idp:ListUsersInGroup',
  ];

  // 인스턴스 롤에 붙은 모든 정책 문에서 cognito-idp 액션을 모은다.
  const granted = new Set<string>();
  for (const policy of Object.values(t.findResources('AWS::IAM::Policy'))) {
    for (const stmt of ((policy as any).Properties?.PolicyDocument?.Statement ?? [])) {
      const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
      for (const a of actions) {
        if (typeof a === 'string' && a.startsWith('cognito-idp:')) granted.add(a);
      }
    }
  }
  for (const action of required) {
    assert.ok(granted.has(action),
      `instance role must allow ${action} — /admin/users 502s with AccessDeniedException without it`);
  }
  // 와일드카드로 뭉개지 않았는지: 필요한 것만 준다(최소 권한).
  assert.ok(!granted.has('cognito-idp:*'),
    'do not grant cognito-idp:* — list the actions the backend actually calls');
  console.log('OK  hosting: instance role has every Cognito Admin action /admin/users calls');
}
