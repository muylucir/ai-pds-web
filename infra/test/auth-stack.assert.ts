import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { AipdsAuthStack } from '../lib/aipds-auth-stack';
import {
  GROUP_ADMIN, GROUP_PM, SEED_ADMIN_EMAIL, SEED_PASSWORD, SEED_PM_EMAIL,
  usernameForEmail,
} from '../lib/auth-client-config';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

const app = new cdk.App();
const stack = new AipdsAuthStack(app, 'Auth', { env: ENV });
const t = Template.fromStack(stack);

// --- self-signup 차단: 이 요구사항의 실체는 이 한 필드다. ---
t.hasResourceProperties('AWS::Cognito::UserPool', {
  AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
});

// --- username은 호출자가 지정한다(AliasAttributes), Cognito 자동 생성 아님. ---
// UsernameAttributes가 설정되면 Cognito가 username을 UUID로 만들어 시딩과
// 관리 API 호출이 비결정적이 된다.
t.hasResourceProperties('AWS::Cognito::UserPool', {
  AliasAttributes: ['email'],
});
const pools = t.findResources('AWS::Cognito::UserPool');
const poolProps = Object.values(pools)[0].Properties;
assert.ok(
  poolProps.UsernameAttributes === undefined,
  'UsernameAttributes must be absent — it would make Cognito auto-generate usernames',
);

// --- 비밀번호 정책: 시드 비밀번호가 통과해야 한다. ---
t.hasResourceProperties('AWS::Cognito::UserPool', {
  Policies: {
    PasswordPolicy: {
      MinimumLength: 8,
      RequireLowercase: true,
      RequireUppercase: true,
      RequireNumbers: true,
      RequireSymbols: true,
    },
  },
});

// --- 계정 복구는 관리자 전용 (메일을 보내지 않으므로 자가 재설정 불가). ---
t.hasResourceProperties('AWS::Cognito::UserPool', {
  AccountRecoverySetting: {
    RecoveryMechanisms: [{ Name: 'admin_only', Priority: 1 }],
  },
});

// --- 그룹 2개 + precedence. ---
t.resourceCountIs('AWS::Cognito::UserPoolGroup', 2);
t.hasResourceProperties('AWS::Cognito::UserPoolGroup', {
  GroupName: 'admin', Precedence: 0,
});
t.hasResourceProperties('AWS::Cognito::UserPoolGroup', {
  GroupName: 'pm', Precedence: 10,
});

// --- Hosted UI v2 (managed login). v1이면 브랜딩 디자이너가 아닌 구 UI가 뜬다. ---
t.hasResourceProperties('AWS::Cognito::UserPoolDomain', {
  ManagedLoginVersion: 2,
  Domain: 'aipds-123456789012-ap-northeast-2',
});
// v2는 브랜딩 스타일 레코드가 있어야 정상 렌더된다.
t.hasResourceProperties('AWS::Cognito::ManagedLoginBranding', {
  UseCognitoProvidedValues: true,
});
// ClientId는 CDK 타입에서 옵셔널이지만 Cognito API에는 필수다 — 브랜딩 스타일은
// 앱 클라이언트 단위로 연결된다. 빠뜨리면 합성/유닛 테스트는 통과하고 실배포가
// "Value null at 'clientId' failed to satisfy constraint"로 죽는다(실측:
// AipdsAuthStack ROLLBACK). 실제 클라이언트를 가리키는지까지 확인한다.
const brandings = t.findResources('AWS::Cognito::ManagedLoginBranding');
const brandingProps = Object.values(brandings)[0].Properties;
assert.ok(
  brandingProps.ClientId !== undefined,
  'ManagedLoginBranding.ClientId must be set — Cognito rejects null even though CDK types it optional',
);
const clientLogicalIds = Object.keys(t.findResources('AWS::Cognito::UserPoolClient'));
assert.deepStrictEqual(
  brandingProps.ClientId,
  { Ref: clientLogicalIds[0] },
  'ManagedLoginBranding.ClientId must reference the web app client',
);

// --- 앱 클라이언트: authorization code grant만, 시크릿 있음. ---
t.hasResourceProperties('AWS::Cognito::UserPoolClient', {
  GenerateSecret: true,
  AllowedOAuthFlows: ['code'],
  AllowedOAuthFlowsUserPoolClient: true,
  AllowedOAuthScopes: Match.arrayWith(['openid', 'email', 'profile']),
  CallbackURLs: ['http://localhost:3000/api/auth/callback'],
  LogoutURLs: ['http://localhost:3000/login'],
});
// implicit grant는 토큰을 URL 프래그먼트로 흘리므로 절대 허용하지 않는다.
const clients = t.findResources('AWS::Cognito::UserPoolClient');
const clientProps = Object.values(clients)[0].Properties;
assert.ok(
  !clientProps.AllowedOAuthFlows.includes('implicit'),
  'implicit grant must not be allowed',
);

// --- 시딩: 계정 2개 × 단계 3개 = 커스텀 리소스 6개. ---
// AwsCustomResource가 만드는 각 Custom::AWS의 Create/Update 필드는 SDK 호출을
// 기술하는 JSON을 Fn::Join으로 조립한 값이다(UserPoolId 부분만 { Ref: ... }).
// 문자열을 이어붙인 뒤 파싱하면 실제 service/action/parameters를 얻을 수
// 있다 — 이걸 파싱해야 "어떤 리소스가 어떤 계정·그룹과 쌍을 이루는지"를
// 검증할 수 있다. 전부를 JSON.stringify해 하나의 문자열로 뭉친 뒤
// includes()로 찾으면, admin/pm의 그룹이 서로 바뀌어도, Permanent가
// 빠져도, SUPPRESS가 한쪽에만 있어도 통과해버린다 — 그래서 리소스별로
// 파싱해서 확인한다.
function parseSdkCall(field: any): { action: string; parameters: any } | undefined {
  if (!field) return undefined;
  const parts = field['Fn::Join'][1];
  const joined = parts.map((p: any) => (typeof p === 'string' ? p : '')).join('');
  return JSON.parse(joined);
}

const customResources = t.findResources('Custom::AWS');
const resourceList = Object.values(customResources);
assert.strictEqual(
  resourceList.length, 6,
  `expected 6 seed custom resources, got ${resourceList.length}`,
);

const calls = resourceList.map((r: any) => ({
  create: parseSdkCall(r.Properties.Create),
  update: parseSdkCall(r.Properties.Update),
}));

function assertSeeding(email: string, group: string) {
  // Username은 이메일이 아니라 로컬파트다 — Cognito가 email-alias 풀에서 이메일
  // 형식 Username을 거부한다(실측: 스택 롤백). 세 호출이 모두 같은 값을 써야
  // 비밀번호/그룹이 방금 만든 계정에 적용된다.
  const username = usernameForEmail(email);

  // 1) adminCreateUser: 이 계정의 Username으로, 초대 메일 억제, 이메일 검증됨.
  const createCalls = calls
    .map((c) => c.create)
    .filter((c) => c?.action === 'adminCreateUser' && c?.parameters.Username === username);
  assert.strictEqual(
    createCalls.length, 1,
    `expected exactly 1 adminCreateUser for ${email}, got ${createCalls.length}`,
  );
  assert.strictEqual(
    createCalls[0]!.parameters.MessageAction, 'SUPPRESS',
    `${email}: invite email must be suppressed`,
  );
  const emailVerified = createCalls[0]!.parameters.UserAttributes.find(
    (a: any) => a.Name === 'email_verified',
  );
  assert.strictEqual(
    emailVerified?.Value, 'true',
    `${email}: email_verified must be true`,
  );
  // Username은 이메일이 아니지만 email 속성은 원문 이메일이어야 한다 — 그게
  // alias 사인인의 대상이다. 이걸 로컬파트로 바꾸면 로그인이 불가능해진다.
  const emailAttr = createCalls[0]!.parameters.UserAttributes.find(
    (a: any) => a.Name === 'email',
  );
  assert.strictEqual(
    emailAttr?.Value, email,
    `${email}: the email attribute must keep the full address (alias sign-in target)`,
  );

  // 2) adminSetUserPassword: 이 계정의 Username으로, 시드 비밀번호, Permanent — 아니면
  // FORCE_CHANGE_PASSWORD 상태로 남아 강제 비밀번호 변경을 요구한다.
  const passwordCalls = calls
    .map((c) => c.create)
    .filter((c) => c?.action === 'adminSetUserPassword' && c?.parameters.Username === username);
  assert.strictEqual(
    passwordCalls.length, 1,
    `expected exactly 1 adminSetUserPassword for ${email}, got ${passwordCalls.length}`,
  );
  assert.strictEqual(
    passwordCalls[0]!.parameters.Password, SEED_PASSWORD,
    `${email}: seed password must be set`,
  );
  assert.strictEqual(
    passwordCalls[0]!.parameters.Permanent, true,
    `${email}: password must be made permanent (no forced change)`,
  );

  // 3) adminAddUserToGroup: 이 계정의 Username이 정확히 이 그룹과 쌍을 이뤄야
  // 한다 — 역할의 유일한 출처이므로 admin/pm이 뒤바뀌면 그대로 권한 사고다.
  const groupCalls = calls
    .map((c) => c.create)
    .filter((c) => c?.action === 'adminAddUserToGroup' && c?.parameters.Username === username);
  assert.strictEqual(
    groupCalls.length, 1,
    `expected exactly 1 adminAddUserToGroup for ${email}, got ${groupCalls.length}`,
  );
  assert.strictEqual(
    groupCalls[0]!.parameters.GroupName, group,
    `${email}: must be added to group '${group}', got '${groupCalls[0]!.parameters.GroupName}'`,
  );
}

assertSeeding(SEED_ADMIN_EMAIL, GROUP_ADMIN);
assertSeeding(SEED_PM_EMAIL, GROUP_PM);

// 전역 불변식: 어떤 Admin* 호출에도 이메일 형식 Username이 없어야 한다. Cognito가
// 거부하는 조건은 그것 하나이므로, 규칙(로컬파트)이 아니라 이 불변식을 직접
// 단정한다 — 규칙이 나중에 바뀌어도 배포 실패는 막힌다.
for (const c of calls) {
  for (const call of [c.create, c.update]) {
    const u = call?.parameters?.Username;
    if (typeof u === 'string') {
      assert.ok(
        !u.includes('@'),
        `Cognito rejects email-shaped usernames in an email-alias pool; got '${u}' in ${call?.action}`,
      );
    }
  }
}

// --- 출력: 백엔드/프론트 env로 쓰인다. ---
const outputs = t.findOutputs('*');
for (const key of ['UserPoolId', 'UserPoolClientId', 'HostedUiDomain']) {
  assert.ok(outputs[key], `output ${key} must exist`);
}
// 클라이언트 시크릿은 출력하지 않는다 — EC2가 부팅 시 조회한다.
assert.ok(!outputs.ClientSecret, 'client secret must NOT be a CfnOutput');

// --- 스택이 노출하는 참조 (HostingStack이 쓴다). ---
assert.ok(stack.userPool, 'userPool must be exposed');
assert.ok(stack.userPoolClient, 'userPoolClient must be exposed');
assert.ok(
  stack.hostedUiDomain.includes('auth.ap-northeast-2.amazoncognito.com'),
  `hostedUiDomain must be the full auth domain, got ${stack.hostedUiDomain}`,
);

console.log('OK  auth stack: no-self-signup + alias username + groups + managed login v2 + code-only client + per-account seed pairing (create/suppress, permanent password, correct group)');

// --- 시드 권한은 롤 생성 시점에 인라인으로 붙는다 (IAM 최종 일관성) ---
//
// **실측(2026-08-21, AipdsAuthStack 첫 생성 ROLLBACK).** 시드 6개 호출 중
// `adminAddUserToGroup`(pm) 하나가 AccessDenied로 죽고 스택이 롤백됐다:
//
//   01:03:06  PutRolePolicy  SeedPmGroupCustomResourcePolicy    (성공)
//   01:03:22  AdminAddUserToGroup  pm     -> AccessDenied
//   01:03:23  AdminAddUserToGroup  admin  -> 성공
//
// 같은 롤, 같은 액션, 같은 리소스, 같은 시각에 붙은 같은 모양의 정책인데 1초 차이로
// 하나는 거부되고 하나는 통과했다 — IAM 인가 경로의 최종 일관성이다. `DependsOn`은
// API **호출 순서**만 보장하고 IAM이 새 정책을 반영했다는 것은 보장하지 않는다.
//
// `AwsCustomResourcePolicy.fromSdkCalls`는 호출마다 별도 `AWS::IAM::Policy`를 만들고
// 그 각각이 소비 직전(실측 16초 전)에 쓰인다 — 호출이 여섯이면 경쟁도 여섯 번이고,
// 그중 하나만 져도 스택 전체가 롤백된다. 시드가 늘면 실패 확률이 함께 오른다.
//
// 그래서 권한을 **롤의 inline policy**로 옮긴다. 그러면 IAM 쓰기가 `CreateRole`
// 시점이 되고, 그 롤은 provider Lambda보다 먼저 있어야 하며 Lambda는 어떤 커스텀
// 리소스보다 먼저 있어야 한다 — 실측 타임라인에서 40~80초의 여유가 생긴다. 경쟁을
// 여섯에서 0으로 줄이는 것이 아니라, **정책이 존재하는 시점을 리소스 생성 순서로
// 보장**하는 것이 요점이다.
{
  const seedPolicies = Object.entries(t.findResources('AWS::IAM::Policy'))
    .filter(([id]) => id.startsWith('Seed'));
  assert.deepStrictEqual(
    seedPolicies.map(([id]) => id), [],
    'seed permissions must not come from per-call AWS::IAM::Policy resources — each one '
    + 'is written moments before its consumer runs and races IAM convergence. Found: '
    + seedPolicies.map(([id]) => id).join(', '),
  );

  // 권한은 provider 롤의 inline policy에 있어야 한다. 세 액션 전부 — 하나라도
  // 빠지면 그 단계에서 같은 롤백이 난다.
  const roles = Object.values(t.findResources('AWS::IAM::Role'))
    .filter((r) => JSON.stringify(r.Properties?.Policies ?? '').includes('cognito-idp:'));
  assert.strictEqual(
    roles.length, 1,
    `expected exactly 1 role carrying the seed permissions inline, got ${roles.length}`,
  );
  const statements = (roles[0]!.Properties.Policies as any[])
    .flatMap((p) => p.PolicyDocument.Statement as any[]);
  for (const action of ['cognito-idp:AdminCreateUser',
                        'cognito-idp:AdminSetUserPassword',
                        'cognito-idp:AdminAddUserToGroup']) {
    const hit = statements.find((s) => {
      const a = s.Action;
      return Array.isArray(a) ? a.includes(action) : a === action;
    });
    assert.ok(hit, `seed role must allow ${action} inline at role creation`);
    // 풀 ARN으로 좁혀야 한다 — 와일드카드면 다른 풀의 사용자도 만질 수 있다.
    assert.ok(
      JSON.stringify(hit.Resource).includes('UserPool'),
      `${action} must be scoped to this user pool, got ${JSON.stringify(hit.Resource)}`,
    );
  }
}
console.log('OK  auth stack: seed permissions are inline on the provider role (no per-call policy racing IAM)');
