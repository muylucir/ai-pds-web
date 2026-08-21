import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as iam from 'aws-cdk-lib/aws-iam';
import { usernameForEmail } from './auth-client-config';

export interface SeedUserProps {
  userPool: cognito.IUserPool;
  email: string;
  group: string;
  password: string;
  /** 시드 호출 전부가 공유하는 provider 롤. `seedProviderRole`이 만든다. */
  role: iam.IRole;
}

/** 시드 호출 세 종에 필요한 Cognito 액션. 이 목록이 곧 시드의 권한 경계다. */
const SEED_ACTIONS = [
  'cognito-idp:AdminCreateUser',
  'cognito-idp:AdminSetUserPassword',
  'cognito-idp:AdminAddUserToGroup',
];

// AwsCustomResource provider Lambda의 실행 롤. **권한을 인라인으로 들고 있다.**
//
// **왜 fromSdkCalls를 쓰지 않는가(2026-08-21 실측).** 그 헬퍼는 호출마다 별도
// `AWS::IAM::Policy`를 만들고, CloudFormation은 그것을 소비 직전에 쓴다(실측 16초
// 전). `DependsOn`은 API 호출 순서만 보장하고 IAM 인가 경로가 새 정책을 반영했다는
// 것은 보장하지 않으므로, 그 16초가 최종 일관성과의 경쟁이 된다. AipdsAuthStack의
// 첫 생성이 정확히 그렇게 죽었다:
//
//   01:03:06  PutRolePolicy  SeedPmGroupCustomResourcePolicy   성공
//   01:03:22  AdminAddUserToGroup  pm     -> AccessDenied
//   01:03:23  AdminAddUserToGroup  admin  -> 성공
//
// 같은 롤·같은 액션·같은 리소스에 같은 시각 붙은 같은 모양의 정책인데 1초 차이로
// 하나가 졌다. 시드 계정 둘이면 호출이 여섯이고 경쟁도 여섯 번인데, **하나만 져도
// 스택 전체가 롤백된다** — 시드를 늘리면 실패 확률이 함께 오른다.
//
// 인라인으로 옮기면 IAM 쓰기가 `CreateRole` 시점이 된다. 그 롤은 provider Lambda보다
// 먼저 존재해야 하고 Lambda는 어떤 커스텀 리소스보다 먼저 존재해야 하므로, 리소스
// 생성 순서가 곧 여유가 된다(실측 타임라인에서 40~80초).
//
// 기본 실행 롤을 대체하므로 `AWSLambdaBasicExecutionRole`을 직접 넣는다 — 빼면 이
// Lambda의 로그가 사라지고, 이 결함의 진단 경로가 바로 그 로그였다.
export function seedProviderRole(scope: Construct,
                                 userPool: cognito.IUserPool): iam.Role {
  return new iam.Role(scope, 'SeedProviderRole', {
    assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    managedPolicies: [
      iam.ManagedPolicy.fromAwsManagedPolicyName(
        'service-role/AWSLambdaBasicExecutionRole'),
    ],
    inlinePolicies: {
      // `inlinePolicies`여야 롤 리소스의 `Policies` 속성으로 렌더된다 —
      // `addToPolicy`는 별도 `AWS::IAM::Policy`(DefaultPolicy)를 만들어 위와 같은
      // 경쟁으로 되돌아간다.
      SeedCognitoAdmin: new iam.PolicyDocument({
        statements: [new iam.PolicyStatement({
          actions: SEED_ACTIONS,
          resources: [userPool.userPoolArn],
        })],
      }),
    },
  });
}

// cdk deploy 한 번으로 '로그인 가능한' 계정을 만든다.
//
// 왜 CfnUserPoolUser를 쓰지 않는가: 그 L1은 사용자를 FORCE_CHANGE_PASSWORD
// 상태로만 만들 수 있고 비밀번호를 확정(Permanent)할 방법이 없다. 첫 로그인에서
// 비밀번호 변경을 요구하지 않아야 한다는 요구사항 때문에 AdminSetUserPassword가
// 필요하고, 그건 커스텀 리소스로만 호출할 수 있다.
export function seedUser(scope: Construct, id: string, props: SeedUserProps): void {
  const { userPool, email, group, password, role } = props;
  // Username은 이메일이 아니라 로컬파트다 — 이 풀은 AliasAttributes=[email]이고
  // Cognito는 그 경우 이메일 형식 Username을 거부한다(실측: 스택 롤백).
  // 규칙의 근거와 백엔드와의 동기화 요구는 usernameForEmail 주석 참조.
  const username = usernameForEmail(email);

  // 1) 사용자 생성. 이메일 발송 없음(SUPPRESS) — 초대는 관리 페이지가 임시
  // 비밀번호를 화면에 보여주는 방식이고, 시드 계정은 비밀번호가 이미 알려져 있다.
  // email_verified=true는 선택이 아니다: alias(email) 사인인은 검증된 이메일에만
  // 동작한다.
  const create = new cr.AwsCustomResource(scope, `${id}Create`, {
    onCreate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminCreateUser',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        MessageAction: 'SUPPRESS',
        UserAttributes: [
          { Name: 'email', Value: email },
          { Name: 'email_verified', Value: 'true' },
        ],
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-create`),
      // 재배포 시 사용자는 이미 있다. 그걸 실패로 보면 스택이 롤백된다.
      ignoreErrorCodesMatching: 'UsernameExistsException',
    },
    role,
    installLatestAwsSdk: false,
  });

  // 2) 비밀번호를 확정(Permanent)한다 → 상태가 CONFIRMED가 되어 첫 로그인에서
  // 변경을 요구하지 않는다. onUpdate에도 걸어 재배포마다 알려진 값으로 되돌린다.
  //
  // Username에 1단계의 응답이 아니라 위에서 유도한 같은 값을 쓴다: 1단계가
  // UsernameExistsException으로 무시되면 응답 필드가 비어 getResponseField가
  // 깨진다. 풀이 AliasAttributes(호출자 지정 username)라서 이렇게 할 수 있다.
  const setPassword = new cr.AwsCustomResource(scope, `${id}Password`, {
    onCreate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminSetUserPassword',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        Password: password,
        Permanent: true,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-password`),
    },
    onUpdate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminSetUserPassword',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        Password: password,
        Permanent: true,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-password`),
    },
    role,
    installLatestAwsSdk: false,
  });
  setPassword.node.addDependency(create);

  // 3) 그룹 배정 = 역할 부여. 이미 속해 있으면 no-op(멱등).
  const addToGroup = new cr.AwsCustomResource(scope, `${id}Group`, {
    onCreate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminAddUserToGroup',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        GroupName: group,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-group-${group}`),
    },
    onUpdate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminAddUserToGroup',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: username,
        GroupName: group,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-group-${group}`),
    },
    role,
    installLatestAwsSdk: false,
  });
  addToGroup.node.addDependency(setPassword);
}
