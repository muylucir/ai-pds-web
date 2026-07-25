import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { PathfinderAuthStack } from '../lib/pathfinder-auth-stack';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

const app = new cdk.App();
const stack = new PathfinderAuthStack(app, 'Auth', { env: ENV });
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
  Domain: 'pathfinder-123456789012-ap-northeast-2',
});
// v2는 브랜딩 스타일 레코드가 있어야 정상 렌더된다.
t.hasResourceProperties('AWS::Cognito::ManagedLoginBranding', {
  UseCognitoProvidedValues: true,
});

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
const customResources = t.findResources('Custom::AWS');
assert.strictEqual(
  Object.keys(customResources).length, 6,
  `expected 6 seed custom resources, got ${Object.keys(customResources).length}`,
);
const bodies = JSON.stringify(customResources);
for (const email of ['admin@pathfinder.local', 'pm@pathfinder.local']) {
  assert.ok(bodies.includes(email), `seed user ${email} must be created`);
}
assert.ok(bodies.includes('PathFinder2026!@'), 'seed password must be set');
assert.ok(bodies.includes('adminSetUserPassword'), 'password must be made permanent');
assert.ok(bodies.includes('adminAddUserToGroup'), 'seed users must be grouped');
assert.ok(bodies.includes('SUPPRESS'), 'invite emails must be suppressed');

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

console.log('OK  auth stack: no-self-signup + alias username + groups + managed login v2 + code-only client + 6 seed resources');
