import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import {
  ACCESS_TOKEN_VALIDITY_MINUTES, CLIENT_NAME, COGNITO_ADMIN_SCOPE,
  GROUP_ADMIN, GROUP_PM, ID_TOKEN_VALIDITY_MINUTES,
  LOCAL_APP_URL, OAUTH_SCOPES, REFRESH_TOKEN_VALIDITY_MINUTES,
  SEED_ADMIN_EMAIL, SEED_PM_EMAIL,
  callbackUrls, logoutUrls,
} from './auth-client-config';
import { seedProviderRole, seedUser } from './seed-users';

// OAuthScope는 생성자가 private이고 정적 상수(+custom())만 노출한다 —
// 문자열을 그대로 넘길 수 없어 매핑이 필요하다. 문자열 목록의 출처는
// auth-client-config.ts 하나로 유지한다(콜백 주입 커스텀 리소스도 그걸 쓴다).
const SCOPE_MAP: Record<string, cognito.OAuthScope> = {
  openid: cognito.OAuthScope.OPENID,
  email: cognito.OAuthScope.EMAIL,
  profile: cognito.OAuthScope.PROFILE,
  [COGNITO_ADMIN_SCOPE]: cognito.OAuthScope.COGNITO_ADMIN,
};

// 임시 비밀번호 유효기간. 시드 계정이 이제 임시 비밀번호로 만들어지므로 이 창이
// 곧 "배포한 계정으로 로그인할 수 있는 기간"이다. Cognito 기본값은 7일인데,
// 배포와 워크숍 사이가 그보다 길면 시드 계정이 로그인 불가가 되고 증상은
// "비밀번호는 맞는데 안 들어가진다"로만 보인다.
//
// 대가: 관리 페이지가 발급하는 초대 계정의 임시 비밀번호도 같은 창을 갖는다 —
// 이 값은 풀 정책이라 계정별로 다르게 둘 수 없다. 초대는 관리자가 즉시
// 전달하므로 실질 위험은 "전달받고 30일간 쓰지 않은 임시 비밀번호"에 한정된다.
const TEMP_PASSWORD_VALIDITY_DAYS = 30;

// 배포 시점 임시 비밀번호가 만족해야 하는 형태 = 풀의 비밀번호 정책.
//
// 왜 CloudFormation에 검사를 맡기는가: 정책을 위반한 값은 `AdminSetUserPassword`가
// `InvalidPasswordException`으로 거부하고 **스택 전체가 롤백된다**. 배포가 몇 분
// 진행된 뒤에 나는 그 실패를 파라미터 검증 단계로 끌어당긴다.
//
// 공백을 허용하지 않는다(`\S`): Cognito 자체는 받아주지만, 이 값은 셸 명령줄로
// 전달되고 사람이 메신저로 옮겨 적는 값이다.
const SEED_PASSWORD_PATTERN =
  '(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])'
  + '(?=.*[\\^$*.\\[\\]{}()?"!@#%&/\\\\,><\':;|_~`+=-])'
  + '\\S{8,256}';

export class AipdsAuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly hostedUiDomain: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // --- 시드 계정의 임시 비밀번호: 배포 명령이 준다 ---
    //
    //   npx cdk deploy --all --parameters AipdsAuthStack:SeedPassword='...'
    //
    // 소스 상수가 아닌 이유: 상수는 리포에 커밋되고 CloudFormation 템플릿·스택
    // 이벤트에도 평문으로 남는다. `noEcho`가 그 세 경로를 모두 끊는다 —
    // 템플릿에는 `Ref`만 남는다.
    //
    // 기본값을 두지 않는다: 기본값은 곧 하드코딩된 비밀번호이고, 조용히 배포된다.
    // 빠뜨리면 CloudFormation이 배포 시작 시점에 거부한다.
    //
    // ⚠️ 남는 노출 한 곳: AwsCustomResource의 provider Lambda가 수신 이벤트를
    // 로그에 남기므로 그 로그 그룹에 값이 한 번 찍힌다(`Logging.withDataHidden()`은
    // API 응답만 가린다). 이 값이 첫 로그인에 반드시 교체되는 임시 비밀번호라서
    // 감수하는 노출이다 — 영구 비밀번호였다면 감수할 수 없다.
    //
    // 재배포 시 다시 적지 않아도 된다: `cdk deploy`의 `--previous-parameters`가
    // 기본 true다. 그리고 비밀번호를 심는 커스텀 리소스에는 `onUpdate`가 없으므로
    // (seed-users.ts) 값이 바뀌어도 재배포가 기존 계정을 되돌리지 않는다 —
    // 재발급은 관리 페이지의 '비밀번호 재설정'이 담당한다.
    const seedPassword = new cdk.CfnParameter(this, 'SeedPassword', {
      type: 'String',
      noEcho: true,
      minLength: 8,
      maxLength: 256,
      allowedPattern: SEED_PASSWORD_PATTERN,
      description:
        'Temporary password for the seeded admin/pm accounts. Must satisfy the pool '
        + 'policy (8+ chars, upper, lower, digit, symbol, no spaces). Both accounts '
        + 'are created in FORCE_CHANGE_PASSWORD state, so each user replaces this at '
        + 'first login.',
      constraintDescription:
        'at least 8 characters with an uppercase letter, a lowercase letter, a digit, '
        + 'a symbol, and no spaces',
    });

    // --- User Pool ---
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'aipds',
      // 이 한 줄이 "self signup 금지"의 실체다 → CFN
      // AdminCreateUserConfig.AllowAdminCreateUserOnly: true.
      // Hosted UI에 회원가입 링크 자체가 렌더되지 않는다.
      selfSignUpEnabled: false,
      // username: true를 함께 켜면 CDK가 AliasAttributes로 합성해 호출자가
      // Username을 지정할 수 있다. { email: true }만 두면 UsernameAttributes가
      // 되어 Cognito가 username을 UUID로 자동 생성하고, 그러면 CDK 커스텀
      // 리소스가 재배포마다 그 값을 알 수 없어 시딩이 비결정적이 된다.
      // 사용자는 어느 쪽이든 이메일로 로그인한다.
      signInAliases: { username: true, email: true },
      signInCaseSensitive: false,
      autoVerify: { email: true },
      standardAttributes: {
        email: { required: true, mutable: true },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
        tempPasswordValidity: cdk.Duration.days(TEMP_PASSWORD_VALIDITY_DAYS),
      },
      mfa: cognito.Mfa.OFF,
      // 이 앱은 메일을 전혀 보내지 않으므로 자가 재설정 코드를 전달할 경로가
      // 없다. 재설정은 관리 페이지에서 관리자가 한다.
      accountRecovery: cognito.AccountRecovery.NONE,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- 역할 = 그룹. 이것이 역할의 유일한 출처다(커스텀 속성 없음). ---
    new cognito.CfnUserPoolGroup(this, 'AdminGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: GROUP_ADMIN,
      description: 'AI-PDS 관리자 — PM 권한 + 사용자 관리',
      precedence: 0,
    });
    new cognito.CfnUserPoolGroup(this, 'PmGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: GROUP_PM,
      description: 'AI-PDS PM — 프로젝트 전체 접근, 사용자 관리 제외',
      precedence: 10,
    });

    // --- Hosted UI v2 (managed login) ---
    // 도메인 프리픽스는 계정·리전 안에서 유일해야 한다.
    const domainPrefix = `aipds-${account}-${region}`;
    const domain = this.userPool.addDomain('HostedUi', {
      cognitoDomain: { domainPrefix },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });
    this.hostedUiDomain = `${domainPrefix}.auth.${region}.amazoncognito.com`;

    // --- 앱 클라이언트 ---
    // confidential(시크릿 있음): 코드 교환이 서버사이드(Next route handler)라
    // 시크릿을 안전히 보관할 수 있고, 두면 client_id만 훔친 코드 가로채기가 막힌다.
    // 콜백은 localhost만 — 실제 CloudFront 도메인은 HostingStack이 덧붙인다(§3.5).
    this.userPoolClient = this.userPool.addClient('WebClient', {
      userPoolClientName: CLIENT_NAME,
      generateSecret: true,
      authFlows: { userSrp: false, userPassword: false },
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: false },
        scopes: OAUTH_SCOPES.map((s) => SCOPE_MAP[s]),
        callbackUrls: callbackUrls([LOCAL_APP_URL]),
        logoutUrls: logoutUrls([LOCAL_APP_URL]),
      },
      // 값의 출처는 auth-client-config.ts 하나뿐이다 — HostingStack의
      // UpdateUserPoolClient 재전송(PUT 시맨틱)이 같은 값을 다시 써야
      // 재배포 때마다 유효기간이 리셋되지 않는다.
      accessTokenValidity: cdk.Duration.minutes(ACCESS_TOKEN_VALIDITY_MINUTES),
      idTokenValidity: cdk.Duration.minutes(ID_TOKEN_VALIDITY_MINUTES),
      refreshTokenValidity: cdk.Duration.minutes(REFRESH_TOKEN_VALIDITY_MINUTES),
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
    });

    // v2는 브랜딩 스타일 레코드가 있어야 정상 렌더된다(콘솔이 자동으로 하는 일).
    // 없으면 로그인 페이지가 깨진 채로 뜬다.
    //
    // clientId는 CDK 타입에서 옵셔널이지만 Cognito API에는 필수다 — 브랜딩
    // 스타일은 user pool이 아니라 앱 클라이언트 단위로 연결된다. 넘기지 않으면
    // 합성과 유닛 테스트는 통과하고 실배포가 "Value null at 'clientId' failed to
    // satisfy constraint"로 죽는다(실측: AipdsAuthStack ROLLBACK).
    // 그래서 이 블록은 반드시 클라이언트 생성 뒤에 온다.
    const branding = new cognito.CfnManagedLoginBranding(this, 'Branding', {
      userPoolId: this.userPool.userPoolId,
      clientId: this.userPoolClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });
    // 도메인이 먼저 있어야 브랜딩을 붙일 대상(managed login)이 존재한다.
    branding.node.addDependency(domain);

    // --- 시드 계정: cdk deploy 한 번으로 로그인 가능해야 한다 ---
    //
    // '로그인 가능'은 '비밀번호가 확정되어 있다'가 아니다. 두 계정은 임시
    // 비밀번호로 만들어지고(FORCE_CHANGE_PASSWORD), 사용자는 첫 로그인에서
    // Hosted UI가 띄우는 화면에서 자기 비밀번호를 정한다 — 초대 계정과 같다.
    //
    // 롤을 여기서 한 번 만들어 두 시드가 공유한다. 시드마다 권한을 만들면 IAM 최종
    // 일관성과 경쟁하고, 실제로 그 경쟁에 져서 첫 배포가 롤백됐다 — 근거는
    // seed-users.ts의 `seedProviderRole` 주석.
    const seedRole = seedProviderRole(this, this.userPool);
    seedUser(this, 'SeedAdmin', {
      userPool: this.userPool,
      email: SEED_ADMIN_EMAIL,
      group: GROUP_ADMIN,
      password: seedPassword.valueAsString,
      role: seedRole,
    });
    seedUser(this, 'SeedPm', {
      userPool: this.userPool,
      email: SEED_PM_EMAIL,
      group: GROUP_PM,
      password: seedPassword.valueAsString,
      role: seedRole,
    });

    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: this.userPoolClient.userPoolClientId,
    });
    new cdk.CfnOutput(this, 'HostedUiDomain', { value: this.hostedUiDomain });
    // 클라이언트 시크릿은 출력하지 않는다 — EC2가 부팅 시
    // describe-user-pool-client로 조회한다(스펙 §3.4).
  }
}
