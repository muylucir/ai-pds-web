import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import { backendPolicyStatements, MODEL } from './backend-permissions';
import { DEPLOY_BRANCH, REPO_URL } from './deploy-source';
import { renderUserData } from './user-data';
import {
  ACCESS_TOKEN_VALIDITY_MINUTES, CLIENT_NAME, EXPLICIT_AUTH_FLOWS, ID_TOKEN_VALIDITY_MINUTES,
  LOCAL_APP_URL, OAUTH_SCOPES, REFRESH_TOKEN_VALIDITY_MINUTES,
  callbackUrls, logoutUrls,
} from './auth-client-config';

export interface HostingStackProps extends cdk.StackProps {
  artifactsBucket: s3.IBucket;
  // 테스트 주입용. 미지정 시 배포 리전의 CloudFront origin-facing 프리픽스
  // 리스트를 fromLookup으로 자동 조회한다.
  cfPrefixListId?: string;
  // 인증. AuthStack이 만든 풀/클라이언트를 받아 (1) EC2에 env로 심고
  // (2) CloudFront 도메인을 콜백 URL로 등록한다.
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
  hostedUiDomain: string;
}

export class PathfinderHostingStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: HostingStackProps) {
    super(scope, id, props);

    // --- VPC: 퍼블릭 서브넷만, NAT 없음(비용 0). ---
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
      ],
    });

    // --- CloudFront origin-facing 프리픽스 리스트 (리전 자동) ---
    const cfPrefixListId =
      props.cfPrefixListId ??
      ec2.PrefixList.fromLookup(this, 'CfOriginFacing', {
        prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
      }).prefixListId;

    // --- SG: 80만, CloudFront 프리픽스 리스트에서만. SSH 없음. ---
    const sg = new ec2.SecurityGroup(this, 'InstanceSg', {
      vpc,
      allowAllOutbound: true, // 패키지 설치 · Bedrock · S3
      // ASCII만: EC2는 GroupDescription의 비ASCII를 거부한다("Character sets
      // beyond ASCII are not supported"). em dash 하나로 스택이 롤백된다(실측).
      description: 'Pathfinder EC2: inbound 80 from CloudFront prefix list only.',
    });
    sg.addIngressRule(
      ec2.Peer.prefixList(cfPrefixListId),
      ec2.Port.tcp(80),
      'CloudFront origin-facing only',
    );

    // --- 비밀 헤더 값 (영숫자 32자) ---
    const headerSecret = new secretsmanager.Secret(this, 'OriginVerifyHeader', {
      description: 'X-Origin-Verify shared secret (CloudFront custom header <-> nginx).',
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
        // 단일 스칼라 시크릿(문자열 그대로) — JSON 아님.
      },
    });

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // --- 인스턴스 롤: 백엔드 공통 권한 + 시크릿 읽기 + SSM 접속 ---
    const role = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
      description: 'Pathfinder EC2: Bedrock + artifacts S3 + read header secret.',
    });
    for (const stmt of backendPolicyStatements(props.artifactsBucket, account)) {
      role.addToPolicy(stmt);
    }
    headerSecret.grantRead(role);
    // Cognito 권한 두 묶음.
    //
    // 1) DescribeUserPoolClient — 부팅 시 클라이언트 시크릿을 Cognito에서 직접
    //    읽는다(§3.4). 템플릿에 평문으로 남기지 않기 위한 선택이고, 그 대가가
    //    이 권한이다.
    //
    // 2) Admin* / ListUsers* — /admin/users(사용자 초대·역할 변경·비밀번호
    //    재설정·비활성화·삭제)가 쓴다. 실측 배포 버그: 이 묶음이 없어서 로그인은
    //    되는데 /admin/users만 502였고, 백엔드 로그에만
    //    "cognito call failed (AccessDeniedException)"이 남았다.
    //    목록은 backend/pathfinder/auth/cognito.py의 _call() 호출과 1:1이어야
    //    한다 — 하나라도 빠지면 그 기능만 502가 되고 화면에서는 원인이 보이지
    //    않는다. cognito-idp:*로 뭉개지 않는다(최소 권한). 두 곳이 어긋나지
    //    않도록 test/hosting-stack.assert.ts가 목록을 고정한다.
    role.addToPolicy(new iam.PolicyStatement({
      actions: [
        'cognito-idp:DescribeUserPoolClient',
        'cognito-idp:ListUsers',
        'cognito-idp:ListUsersInGroup',
        'cognito-idp:AdminCreateUser',
        'cognito-idp:AdminDeleteUser',
        'cognito-idp:AdminDisableUser',
        'cognito-idp:AdminEnableUser',
        'cognito-idp:AdminSetUserPassword',
        'cognito-idp:AdminAddUserToGroup',
        'cognito-idp:AdminRemoveUserFromGroup',
        'cognito-idp:AdminListGroupsForUser',
      ],
      resources: [props.userPool.userPoolArn],
    }));

    // --- 배포할 코드: 리포의 특정 커밋 ---
    //
    // 종전에는 리포 루트를 CDK 에셋(zip)으로 올리고 EC2가 S3에서 내려받았다.
    // 그 방식이 남긴 두 가지가 이 변경의 이유다(자세한 근거는 lib/deploy-source.ts):
    //
    // - 에셋은 **gitignore된 파일까지 싣는다.** 그래서 app-asset-excludes.json
    //   이라는 보정 목록이 필요했고, 그 목록에서 빠진 것이 두 번 사고를 냈다:
    //   개발용 `.claude/CLAUDE.md`가 /opt/pathfinder/.claude/에 실려 에이전트
    //   cwd의 **조상**이 되어 한국어 한 줄이 영어 프로젝트 컨텍스트에 매 턴
    //   들어간 것(d94aaa1), 그리고 개발 박스의 `proto-type/`이 실려 아무도
    //   빌드하지 않은 프로토타입이 "빌드 완료"로 보인 것.
    //   clone은 tracked 파일만 가져오므로 이 실패 **종류**가 사라진다.
    //   그 불변식(의도하지 않은 CLAUDE.md가 앱 트리에 없다)은 이제
    //   test/deployed-tree.assert.ts가 tracked 파일 목록으로 지킨다.
    // - 배포되는 것이 커밋이 아니라 **워킹 트리**였다. 지금은 미커밋 변경이
    //   배포되지 않는다 — 인스턴스는 푸시된 것만 clone한다.
    //
    // 무엇을 clone하는지는 브랜치로만 정한다(SHA를 박지 않는다). 그래서 이
    // 스택은 배포 시점에 git을 호출하지 않고, **user-data가 코드 변경과 무관하게
    // 동일하다** — 즉 cdk deploy는 인스턴스를 교체하지 않고 코드도 갱신하지
    // 않는다. 코드 갱신은 인스턴스의 pathfinder-update가 한다(user-data.ts가
    // 설치한다). 이 선택의 근거는 lib/deploy-source.ts.

    // --- user-data ---
    // CloudFront 도메인은 아래에서 만들어지지만 user-data는 지금 필요하다.
    // Lazy.string으로 합성 마지막에 해석시켜 순환을 끊는다 — CFN 템플릿에서는
    // Fn::GetAtt 참조로 떨어진다.
    let distributionDomain: string | undefined;
    const appUrlToken = cdk.Lazy.string({
      produce: () => `https://${distributionDomain ?? ''}`,
    });

    const userData = ec2.UserData.custom(
      renderUserData({
        region,
        bucketName: props.artifactsBucket.bucketName,
        model: MODEL,
        secretArn: headerSecret.secretArn,
        repoUrl: REPO_URL,
        branch: DEPLOY_BRANCH,
        userPoolId: props.userPool.userPoolId,
        userPoolClientId: props.userPoolClient.userPoolClientId,
        hostedUiDomain: props.hostedUiDomain,
        appUrl: appUrlToken,
      }),
    );

    // --- 인스턴스 (AL2023 x86_64, IMDSv2 강제) ---
    const instance = new ec2.Instance(this, 'Instance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      // 프로토타입 빌드가 이 박스로 들어왔다: 세션마다 claude 서브프로세스
      // (~300-500MB)가 상주하고 next build가 피크 2GB를 쓴다. Graviton은 쓰지
      // 않는다(SDK 번들 바이너리가 x86-64).
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.M7I, ec2.InstanceSize.XLARGE2),
      machineImage: ec2.MachineImage.latestAmazonLinux2023(),
      securityGroup: sg,
      role,
      userData,
      requireImdsv2: true,
      userDataCausesReplacement: true, // 에셋(코드) 변경 시 깨끗한 재부트스트랩
      blockDevices: [{
        deviceName: '/dev/xvda',
        // 프로토타입당 node_modules가 상주한다(실측 ~23MB/건이지만 여유를 둔다).
        volume: ec2.BlockDeviceVolume.ebs(100, { encrypted: true }),
      }],
    });

    // --- 고정 EIP (재배포에도 CloudFront 오리진 도메인 불변) ---
    const eip = new ec2.CfnEIP(this, 'Eip', { domain: 'vpc' });
    new ec2.CfnEIPAssociation(this, 'EipAssoc', {
      allocationId: eip.attrAllocationId,
      instanceId: instance.instanceId,
    });

    // EIP IP -> 퍼블릭 DNS 이름 (CloudFront 오리진은 도메인만 허용; IP 불가).
    //   ec2-<a-b-c-d>.<region>.compute.amazonaws.com  (us-east-1은 compute-1)
    const computeDomain =
      region === 'us-east-1' ? 'compute-1.amazonaws.com' : `${region}.compute.amazonaws.com`;
    const originDnsName =
      `ec2-${cdk.Fn.join('-', cdk.Fn.split('.', eip.attrPublicIp))}.${computeDomain}`;

    // 비밀 헤더 값: CFN dynamic reference({{resolve:secretsmanager:...}})로 주입.
    // 배포 시 CloudFormation이 해석 → 템플릿에는 평문이 남지 않음.
    const origin = new origins.HttpOrigin(originDnsName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      httpPort: 80,
      readTimeout: cdk.Duration.seconds(60), // SSE(백엔드 ping 15s) 여유
      keepaliveTimeout: cdk.Duration.seconds(60),
      customHeaders: {
        'X-Origin-Verify': headerSecret.secretValue.unsafeUnwrap(),
      },
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      // 위 SG와 같은 이유로 ASCII만 쓴다(CloudFront는 비ASCII를 받아줄 수도
      // 있으나, 콘솔 표시용 문자열에 굳이 그 위험을 남기지 않는다).
      comment: 'Pathfinder: CloudFront in front of EC2 (header-authenticated origin).',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200,
      defaultBehavior: {
        origin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
      },
      additionalBehaviors: {
        '/_next/static/*': {
          origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED, // 해시 파일명 → 불변
        },
      },
    });
    distributionDomain = distribution.distributionDomainName;

    // --- 콜백 URL 주입: 순환 의존 해소 ---
    //
    // Cognito는 콜백 URL의 전수 일치만 허용하고(와일드카드 불가), 실제 URL은
    // 이 스택이 만드는 CloudFront 도메인에 달려 있다. AuthStack이 그 도메인을
    // 알려면 이 스택을 참조해야 하고, 이 스택은 이미 AuthStack을 참조하므로
    // 순환이다. 배포 마지막에 클라이언트를 갱신해 끊는다.
    //
    // ⚠️ UpdateUserPoolClient는 PUT 시맨틱이다 — 지정하지 않은 필드를 지운다.
    // 따라서 콜백만 보내는 것이 아니라 클라이언트 설정 전체를 다시 쓴다.
    // 값의 출처는 auth-client-config.ts 하나뿐이라 AuthStack과 어긋나지 않는다.
    // 🔒 의무: AuthStack의 client 정의(addClient 호출)에 필드를 추가하거나
    // 바꿀 때마다 이 아래 parameters도 반드시 같이 고쳐야 한다 — 하나라도
    // 빠뜨리면 그 필드는 다음 배포에서 조용히 기본값으로 리셋된다(예: 토큰
    // 유효기간, ALLOW_REFRESH_TOKEN_AUTH — 실제로 한 번 빠졌던 필드들이다).
    const appUrls = [LOCAL_APP_URL, `https://${distribution.distributionDomainName}`];

    // onCreate와 onUpdate가 같은 호출이어야 한다: onUpdate를 생략하면 재배포 시
    // 갱신되지 않아 도메인이 바뀌어도 콜백이 낡은 채로 남는다. 파라미터를 지역
    // 상수로 뽑아 두 곳이 어긋날 여지를 없앤다.
    const updateClientCall = {
      service: 'CognitoIdentityServiceProvider',
      action: 'updateUserPoolClient',
      parameters: {
        UserPoolId: props.userPool.userPoolId,
        ClientId: props.userPoolClient.userPoolClientId,
        ClientName: CLIENT_NAME,
        CallbackURLs: callbackUrls(appUrls),
        LogoutURLs: logoutUrls(appUrls),
        // PUT 시맨틱이라 아래 필드를 빼면 그 설정이 지워진다 —
        // AuthStack의 클라이언트 정의와 같은 값이어야 한다.
        AllowedOAuthFlows: ['code'],
        AllowedOAuthFlowsUserPoolClient: true,
        AllowedOAuthScopes: OAUTH_SCOPES,
        SupportedIdentityProviders: ['COGNITO'],
        PreventUserExistenceErrors: 'ENABLED',
        EnableTokenRevocation: true,
        // 토큰 유효기간 + 리프레시 인증 플로우. 처음 구현에서 빠졌던 필드들 —
        // 빠지면 재배포마다 1h/1h/30d가 Cognito 기본값으로 리셋되고
        // ALLOW_REFRESH_TOKEN_AUTH가 사라져 /api 프록시의 401 리프레시가
        // 조용히 끊긴다. 상수 출처는 AuthStack과 동일하게 auth-client-config.ts.
        AccessTokenValidity: ACCESS_TOKEN_VALIDITY_MINUTES,
        IdTokenValidity: ID_TOKEN_VALIDITY_MINUTES,
        RefreshTokenValidity: REFRESH_TOKEN_VALIDITY_MINUTES,
        TokenValidityUnits: {
          AccessToken: 'minutes', IdToken: 'minutes', RefreshToken: 'minutes',
        },
        ExplicitAuthFlows: EXPLICIT_AUTH_FLOWS,
      },
      physicalResourceId: cr.PhysicalResourceId.of('pathfinder-callback-urls'),
    };

    const clientUpdate = new cr.AwsCustomResource(this, 'RegisterCallbackUrls', {
      onCreate: updateClientCall,
      onUpdate: updateClientCall,
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: [props.userPool.userPoolArn],
      }),
      installLatestAwsSdk: false,
    });
    // distribution이 만들어진 뒤에 호출돼야 도메인이 확정된다.
    clientUpdate.node.addDependency(distribution);

    new cdk.CfnOutput(this, 'DistributionDomain', {
      value: `https://${distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new cdk.CfnOutput(this, 'EipAddress', { value: eip.attrPublicIp });
  }
}
