import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import { usernameForEmail } from './auth-client-config';

export interface SeedUserProps {
  userPool: cognito.IUserPool;
  email: string;
  group: string;
  password: string;
}

// cdk deploy 한 번으로 '로그인 가능한' 계정을 만든다.
//
// 왜 CfnUserPoolUser를 쓰지 않는가: 그 L1은 사용자를 FORCE_CHANGE_PASSWORD
// 상태로만 만들 수 있고 비밀번호를 확정(Permanent)할 방법이 없다. 첫 로그인에서
// 비밀번호 변경을 요구하지 않아야 한다는 요구사항 때문에 AdminSetUserPassword가
// 필요하고, 그건 커스텀 리소스로만 호출할 수 있다.
export function seedUser(scope: Construct, id: string, props: SeedUserProps): void {
  const { userPool, email, group, password } = props;
  // Username은 이메일이 아니라 로컬파트다 — 이 풀은 AliasAttributes=[email]이고
  // Cognito는 그 경우 이메일 형식 Username을 거부한다(실측: 스택 롤백).
  // 규칙의 근거와 백엔드와의 동기화 요구는 usernameForEmail 주석 참조.
  const username = usernameForEmail(email);
  const policy = cr.AwsCustomResourcePolicy.fromSdkCalls({
    resources: [userPool.userPoolArn],
  });

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
    policy,
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
    policy,
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
    policy,
    installLatestAwsSdk: false,
  });
  addToGroup.node.addDependency(setPassword);
}
