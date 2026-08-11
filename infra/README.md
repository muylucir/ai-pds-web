# Pathfinder Infra (CDK, 기본 ap-northeast-2 / 서울)

세 스택:

- **PathfinderDrillStack** — S3 아티팩트 버킷(`projects/*` + `sessions/*`) +
  백엔드 실행 롤(Bedrock invoke + S3). 인프로세스 Strands 에이전트용.
- **PathfinderAuthStack** — Cognito User Pool + Hosted UI v2 + 역할 그룹 2개 +
  시드 계정 2개. 아래 별도 절에서 설명.
- **PathfinderHostingStack** — VPC + EC2(AL2023 x86_64, m7i.2xlarge) +
  CloudFront. EC2는 CloudFront origin-facing 관리형 프리픽스 리스트(배포 리전
  자동)에서만 80을 받고, CloudFront가 붙이는 비밀 헤더 `X-Origin-Verify`를
  nginx가 검증한다. user-data가 리포 에셋을 받아 백엔드/프론트를 빌드·기동
  한다. 프로토타입 빌드(Claude Agent SDK 에이전트)는 이 백엔드 프로세스
  안에서 직접 돌아간다 — 별도 VM/MicroVM 계층 없음. 배포 마지막에
  AuthStack의 앱 클라이언트에 실제 콜백 URL을 등록한다(아래 "콜백 URL 순환
  의존" 참고).

> **PathfinderVmStack은 제거됐다** (2026-07-25). 프로토타입 빌드는 백엔드
> 프로세스 안에서 돌고, 도쿄 MicroVM·이미지 빌드·토큰 민팅이 모두 사라졌다.
> 이전에 배포한 적이 있다면 한 번 정리한다:
> `npx cdk destroy PathfinderVmStack --region ap-northeast-1`

## PathfinderAuthStack

Cognito User Pool + Hosted UI v2(managed login) + 역할 그룹 2개 + 시드 계정 2개.

- **self-signup 차단** — `selfSignUpEnabled: false`가 CFN
  `AdminCreateUserConfig.AllowAdminCreateUserOnly: true`로 떨어진다. Hosted UI에
  회원가입 링크가 렌더되지 않고, 신규 계정은 `/admin/users`의 초대로만 생긴다.
- **역할** — `admin`(precedence 0) / `pm`(precedence 10) 그룹. 커스텀 속성으로
  role을 두지 않는다.
- **username == 이메일** — `signInAliases: { username: true, email: true }`이므로
  CFN `AliasAttributes: ['email']`이 되고 호출자가 Username을 지정한다.
  `{ email: true }`만 두면 `UsernameAttributes`가 되어 Cognito가 username을 UUID로
  자동 생성하는데, 그러면 CDK 커스텀 리소스가 재배포마다 그 값을 알 수 없어
  시딩이 비결정적이 된다.
- **시드 계정** — `AdminCreateUser`(SUPPRESS) → `AdminSetUserPassword`(Permanent) →
  `AdminAddUserToGroup`. `CfnUserPoolUser` L1으로는 비밀번호를 확정할 수 없어
  첫 로그인마다 변경을 요구하므로 커스텀 리소스를 쓴다.

### 앱 클라이언트 설정의 단일 출처

토큰 유효기간(`ACCESS_TOKEN_VALIDITY_MINUTES` / `ID_TOKEN_VALIDITY_MINUTES` /
`REFRESH_TOKEN_VALIDITY_MINUTES`), 허용 auth flow(`EXPLICIT_AUTH_FLOWS`), 클라이언트
이름(`CLIENT_NAME`)은 시드 계정 상수와 함께 `lib/auth-client-config.ts`에 있다.
AuthStack이 앱 클라이언트를 만들 때와 HostingStack이 배포 마지막에
`UpdateUserPoolClient`로 재전송할 때 반드시 같은 값을 써야 하기 때문이다(아래
"콜백 URL 순환 의존" 참고). 둘이 어긋나면 재배포마다 유효기간·인증 플로우가
조용히 리셋된다.

### 콜백 URL 순환 의존

Cognito는 콜백 URL의 전수 일치만 허용하고(와일드카드 불가) 실제 URL은
HostingStack이 만드는 CloudFront 도메인에 달려 있다. AuthStack은 localhost 콜백만
갖고 배포되고, HostingStack이 배포 마지막에 `UpdateUserPoolClient`로 실제 도메인을
등록한다.

⚠️ **그 API는 PUT 시맨틱이다** — 지정하지 않은 필드를 지운다. 따라서 콜백만 보내는
것이 아니라 클라이언트 설정 전체(콜백/로그아웃 URL, OAuth 스코프, 토큰 유효기간,
auth flow)를 다시 쓴다. 값의 출처는 `lib/auth-client-config.ts` 하나뿐이라
AuthStack과 어긋나지 않는다. **AuthStack의 앱 클라이언트에 필드를 추가하면
HostingStack의 `UpdateUserPoolClient` 재전송에도 반드시 그 필드를 미러링해야
한다** — 누락하면 재배포 시 그 필드가 조용히 지워진다. 이를 사람이 놓쳐도 CI가
잡도록 드리프트 감지 테스트(`test/hosting-stack.assert.ts`)가 두 정의를 비교한다.

### 클라이언트 시크릿

CfnOutput으로 내보내지 않는다. EC2가 부팅 시
`aws cognito-idp describe-user-pool-client`로 직접 읽는다 — Secrets Manager 사본을
만들려면 Cognito가 생성한 값을 CFN 경유로 옮겨야 하고, 그러면 템플릿에 평문으로
남는다. 대가는 인스턴스 롤의 `cognito-idp:DescribeUserPoolClient` 권한이다.

### 시드 비밀번호 경고

`SEED_PASSWORD`(`PathFinder2026!@`)는 CDK 소스의 상수이므로 **CloudFormation
템플릿과 스택 이벤트에 평문으로 남는다.** 데모/워크숍 전용이며 운영 전환 시 반드시
교체한다. `NoEcho` 파라미터로 가리는 대안은 `cdk deploy`마다 값을 넘겨야 해서
"한 번에 배포" 요구와 충돌하므로 택하지 않았다.

### 삭제

`cdk destroy --all` 시 User Pool은 `RemovalPolicy.DESTROY`이므로 **사용자 전원이
함께 사라진다.**

## 리전 (파라미터)
기본 서울(`ap-northeast-2`). 다른 리전은 `CDK_DEPLOY_REGION`으로 오버라이드:
```bash
CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all   # 예: 도쿄
```
프리픽스 리스트 ID는 리전마다 다르지만 `PrefixList.fromLookup`이 배포 리전의
ID를 자동 조회하므로 코드 수정이 필요 없다.

## 테스트
```bash
npm ci
npm test            # user-data 순수함수 + 스택 어서션 (크리덴셜 불필요)
```

## Synth / deploy
```bash
npx cdk synth PathfinderDrillStack      # 크리덴셜 불필요
npx cdk synth PathfinderHostingStack    # 프리픽스 리스트 lookup — 크리덴셜 필요(최초 1회)
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2   # 계정·리전 최초 1회
npx cdk deploy --all --require-approval never
```
> 호스팅 스택은 배포 리전의 CloudFront 프리픽스 리스트를 lookup하므로 첫
> synth/deploy에 계정 크리덴셜이 필요하다. 조회 결과는 `cdk.context.json`에
> 캐시되지만 **커밋하지 않는다**(gitignored) — 항목 키에 계정 ID가 들어가서
> 다른 계정에서는 무효인 캐시이고, 크리덴셜이 있으면 같은 값으로 재생성된다.
> 그래서 클론당 첫 synth 1회는 크리덴셜이 필요하다. EC2 첫 부팅 빌드에 ~5–10분
> 걸리므로 배포 완료 직후 CloudFront가 잠시 502를 반환할 수 있다(정상).

## 출력 (CfnOutputs)
- `PathfinderHostingStack.DistributionDomain` — 접속 URL(`https://dxxxx.cloudfront.net`)
- `PathfinderHostingStack.InstanceId` — SSM 접속: `aws ssm start-session --target <id>`
- `PathfinderHostingStack.EipAddress` — 오리진 IP(디버그)
- `PathfinderDrillStack.ArtifactsBucketName` / `BackendRoleArn` / `Region`
- `PathfinderAuthStack.UserPoolId` / `UserPoolClientId` / `HostedUiDomain`

## 접속 · 검증
- 브라우저 → `DistributionDomain`(HTTPS) → CloudFront → EC2 nginx.
- EC2에는 SSH 포트가 열려있지 않다 — `aws ssm start-session --target <InstanceId>`.
- 오리진 직접 접근은 SG(프리픽스 리스트)로 차단되고, 설령 도달해도 nginx가
  헤더 없으면 403.
