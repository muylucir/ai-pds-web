import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import {
  ACCESS_TOKEN_VALIDITY_MINUTES, CLIENT_NAME, GROUP_ADMIN, GROUP_PM, ID_TOKEN_VALIDITY_MINUTES,
  LOCAL_APP_URL, OAUTH_SCOPES, REFRESH_TOKEN_VALIDITY_MINUTES,
  SEED_ADMIN_EMAIL, SEED_PASSWORD, SEED_PM_EMAIL,
  callbackUrls, logoutUrls,
} from './auth-client-config';
import { seedUser } from './seed-users';

// OAuthScope는 생성자가 private이고 정적 상수(+custom())만 노출한다 —
// 문자열을 그대로 넘길 수 없어 매핑이 필요하다. 문자열 목록의 출처는
// auth-client-config.ts 하나로 유지한다(콜백 주입 커스텀 리소스도 그걸 쓴다).
const SCOPE_MAP: Record<string, cognito.OAuthScope> = {
  openid: cognito.OAuthScope.OPENID,
  email: cognito.OAuthScope.EMAIL,
  profile: cognito.OAuthScope.PROFILE,
};

export class AipdsAuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly hostedUiDomain: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

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
    seedUser(this, 'SeedAdmin', {
      userPool: this.userPool,
      email: SEED_ADMIN_EMAIL,
      group: GROUP_ADMIN,
      password: SEED_PASSWORD,
    });
    seedUser(this, 'SeedPm', {
      userPool: this.userPool,
      email: SEED_PM_EMAIL,
      group: GROUP_PM,
      password: SEED_PASSWORD,
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
