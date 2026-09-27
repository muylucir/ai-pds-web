# AI-PDS Infra (CDK, 기본 ap-northeast-2 / 서울)

**한국어** | [English](README.md)

배포 방법은 루트 [`README.ko.md`](../README.ko.md)에, 배포된 환경의 운영은 앱의 `/manual`에 있다.
이 문서는 **스택이 왜 이렇게 생겼는가**를 다룬다 — 놓쳐도 에러 없이 조용히 깨지는 결정들이다.

## 스택 여섯, 두 묶음

| 스택 | 만드는 것 |
|---|---|
| `AipdsDrillStack` | S3 아티팩트 버킷 + 백엔드 실행 롤(Bedrock invoke + S3) |
| `AipdsAuthStack` | Cognito User Pool + Hosted UI v2 + `admin`/`pm` 그룹 + 시드 계정 2개 |
| `AipdsHostingStack` | VPC + EC2(AL2023 x86_64, m7i.2xlarge, EBS 100GB 암호화) + CloudFront |
| `AipdsPreviewStack` | 프리뷰 전용 CloudFront |
| `AipdsAgentCredsStack` | 샌드박스 프로세스용 Bedrock 전용 `AgentRole` |
| `AipdsUploadCorsStack` | 인스턴스 롤이 버킷 CORS를 읽고 쓰는 권한 |

**앞의 셋**은 서로를 참조하므로(`bin/app.ts`가 버킷·User Pool을 호스팅 스택에 넘긴다) `--all`로 함께
배포하고, 순서는 CDK가 정한다.

**뒤의 셋**이 따로 있는 이유는 한 가지 사실 때문이다: **HostingStack을 배포하면 EC2가 교체될 수
있다.** AMI가 고정되어 있지 않고(`latestAmazonLinux2023()`) user-data가 템플릿의 일부이므로
(`userDataCausesReplacement`), HostingStack을 바꾸는 모든 것이 새 인스턴스를 부를 수 있다. 그래서
인스턴스가 부팅 뒤에 켜는 기능은 HostingStack이 만든 것(인스턴스 롤, 오리진 시크릿, EIP의 DNS
이름)을 *참조만* 하고 고치지 않는 별도 스택에 둔다.

- **값이 인스턴스에 닿는 길.** user-data가 아니다: user-data가 이 스택들의 출력을 참조하면 순환이고
  (이 스택들이 인스턴스 롤을 참조한다), 끊으려고 user-data를 바꾸면 인스턴스가 교체된다. 대신 스택이
  SSM 파라미터를 쓰고(`lib/instance-params.ts`) 인스턴스가 실행 시점에 읽는다 — 부팅 때와 그 뒤
  2분마다 `aipds-harden sync`. 파라미터가 없으면 그 기능은 꺼진 채이고, 켜져 있던 것을 끄지는 않는다.
- **배포하는 두 가지 방식**(`lib/sandbox-stacks.ts`). 새 환경에서는 HostingStack의 값을 스택 간
  참조로 받으므로 `cdk deploy --all` 한 번이면 된다. 떠 있는 환경에서는 네 값을 env로 고정한다
  (`AIPDS_INSTANCE_ROLE_ARN`, `AIPDS_PREVIEW_ORIGIN_DNS`, `AIPDS_ORIGIN_VERIFY_SECRET_ARN`,
  `AIPDS_ARTIFACTS_BUCKET` — 전부 또는 없음). 스택 간 참조가 있으면 `cdk deploy AipdsAgentCredsStack`이
  HostingStack까지 배포하기 때문이다.
- **떠 있는 HostingStack 보호.** `Update:Replace`·`Update:Delete`를 거부하는 스택 정책과 종료 보호를
  걸면, 뜻하지 않은 교체가 실패하고 롤백되는 배포로 바뀐다. 명령은 `/manual`(*인스턴스를 새로
  만들기*)에 있다.

## 별도 스택이 격리하는 것

**프로토타입은 다른 오리진에서 서빙한다.** 프로토타입은 빌드 에이전트가 쓴 코드다. 앱 오리진에서
서빙하면 로그인한 사람의 세션 쿠키가 `/api` 프록시를 거쳐 프로토타입 서버까지 가고, 프로토타입의
JavaScript가 보는 사람의 권한으로 앱 API를 부를 수 있다. 프리뷰 배포는 `/api/proto/*`만 통과시키고
(나머지는 CloudFront Function이 404) 두 번째 비밀 헤더 `X-Preview-Verify`를 붙이며, 백엔드는 그 헤더를
확인한 요청에만 프로토타입을 준다(`backend/aipds/preview_surface.py`). `*.cloudfront.net`은 공개 접미사
목록에 있으므로 브라우저에게 두 배포는 서로 다른 사이트다.

**에이전트와 프로토타입은 다른 사용자, 다른 자격증명으로 돈다.** Discovery·빌드 에이전트는 업로드된
문서를 읽고, 프로토타입은 에이전트가 쓴 코드와 그 npm 의존성을 돌리므로 어느 쪽도 백엔드로 돌지
않는다. `scripts/aipds-launch`(root, sudo)가 이들을 `aipds-agent`·`aipds-proto`로, 자기 프로젝트의
트리만 보이는 systemd unit에서 띄우고, IMDS는 unit마다 막는다. 자격증명은 루프백 엔드포인트
(`backend/aipds/credentials.py`)가 주는 짧은 `AgentRole` 토큰이고, 그 롤은 Bedrock 호출 말고는 아무것도
할 수 없다. 실행 래퍼가 켜져 있는데 기동 점검이 실패하면, 백엔드는 직접 실행으로 물러나지 않고
에이전트 턴과 호스팅을 거부한다.

**인스턴스가 버킷 CORS에 자기 오리진을 유지한다.** 프로젝트 가져오기는 브라우저가 번들을 S3로 직접
PUT하므로 버킷 CORS가 앱 오리진(HostingStack의 CloudFront)을 허용해야 한다. 버킷(DrillStack)은 순환
없이 그 값을 참조할 수 없으므로, `aipds-harden sync`가 PUT 규칙에 `APP_BASE_URL`이 없으면 더한다 —
그 권한을 `AipdsUploadCorsStack`이 준다. DrillStack 재배포가 CORS를 덮어써도 몇 분 안에 돌아온다.

## 백엔드 롤

**버킷 프리픽스 여섯 개**(`lib/backend-permissions.ts`): `projects/*`, `sessions/*`, `surveys/*`,
`models/*`, `design/*`, `imports/*`. 권한은 허용목록이고, 프리픽스가 빠지면 화면에는 일반 오류만,
백엔드 로그에는 `AccessDenied` 한 줄만 남는다. 그중 넷이 프로젝트 프리픽스 **밖**에 있는 데는 이유가
있다: 설문 토큰은 어느 프로젝트 것인지 알기 전에 조회되고, 모델 카탈로그와 디자인 프로필은
프로젝트가 없어도 존재하며, 가져오기 스테이징은 프로젝트 스캔이나 프로젝트 삭제의 `delete_prefix`가
프로젝트 데이터로 착각하면 안 된다.

**Bedrock invoke는 와일드카드다** — `global.anthropic.claude-*` 추론 프로파일과 그에 대응하는 기반
모델. 관리자가 `/admin/models`에서 모델을 추가하므로, 명시 목록이면 등록은 되고 첫 턴에서 실패하는
모델이 생긴다. 롤들은 `aws-marketplace:Subscribe`/`Unsubscribe`/`ViewSubscriptions`도 갖되
`aws:CalledViaLast = bedrock.amazonaws.com` 조건이 붙는다: Bedrock은 계정의 첫 호출 때 모델의
Marketplace 구독을 만들고, 이 조건은 구독이 Bedrock 호출을 거쳐서만 일어나게 한다.

## 배포되는 코드: 워킹 트리가 아니라 푸시된 main

user-data가 공개 리포를 clone해 부팅 시점의 `origin/main`으로 맞춘 뒤 백엔드·프론트를 빌드·기동한다
(`lib/deploy-source.ts`).

- **clone은 tracked 파일만 가져온다.** 워킹 트리를 올리면 gitignore된 파일도 따라간다 — 예를 들어
  개발용 `.claude/CLAUDE.md`는 에이전트 cwd의 *조상*이 되어 매 턴에 주입된다.
  `test/deployed-tree.assert.ts`가 `git ls-files`로 그 불변식을 고정한다.
- **커밋 SHA를 고정하지 않는다.** 배포하는 사람에게 "푸시했는가"를 묻지 않는 대신, `cdk deploy`는
  코드를 갱신하지 않는다 — user-data가 바이트 단위로 같으면 인스턴스가 교체되지 않는다. 갱신은
  인스턴스의 `aipds-update`가 한다(단계별 이유는 스크립트 주석에 있다).

## AipdsAuthStack

- **셀프 사인업 차단** — `selfSignUpEnabled: false`가
  `AdminCreateUserConfig.AllowAdminCreateUserOnly: true`로 렌더된다. 계정은 초대로만 생긴다.
- **역할**은 `admin`(precedence 0)·`pm`(precedence 10) 그룹이고, 커스텀 속성이 아니다.
- **username == email** — `signInAliases: { username: true, email: true }`는
  `AliasAttributes: ['email']`이 되어 호출자가 Username을 정할 수 있다. `{ email: true }`만 두면
  `UsernameAttributes`가 되고, Cognito가 만든 UUID를 시딩 커스텀 리소스가 재배포 사이에 알 수 없다.
- **시드 계정** — `AdminCreateUser`(SUPPRESS) → `AdminSetUserPassword`(`Permanent: false`) →
  `AdminAddUserToGroup`(`lib/seed-users.ts`). 계정이 `FORCE_CHANGE_PASSWORD`로 남으므로 Hosted UI가 첫
  로그인에서 새 비밀번호를 요구한다. 커스텀 리소스에 `onUpdate`가 없으므로 재배포가 사용자가 정한
  비밀번호를 덮어쓰지 않는다.

**앱 클라이언트 설정의 단일 출처.** 토큰 유효기간·인증 플로·클라이언트 이름은
`lib/auth-client-config.ts`에 있다. AuthStack이 이 값으로 앱 클라이언트를 만들고 HostingStack이 같은
값을 다시 보낸다(아래). 둘이 어긋나면 재배포마다 조용히 초기화된다.

**콜백 URL 순환 의존.** Cognito는 정확히 일치하는 콜백 URL만 받고, 실제 URL은 HostingStack의
CloudFront 도메인에 달려 있다. AuthStack은 localhost 콜백만으로 배포되고, HostingStack이 배포 끝에
`UpdateUserPoolClient`로 실제 도메인을 등록한다. ⚠️ **이 API는 PUT 시맨틱이다** — 빠진 필드는
지워진다. 그래서 호출이 클라이언트 설정 전체를 다시 보낸다. **AuthStack의 앱 클라이언트에 필드를
더하면 HostingStack의 재전송에도 넣어야 한다.** 빠뜨리면 다음 배포가 그 필드를 지운다.
`test/hosting-stack.assert.ts`가 둘을 비교한다.

**클라이언트 시크릿**은 CfnOutput이 아니다. 인스턴스가 부팅 때 `describe-user-pool-client`로 읽는다 —
Secrets Manager에 사본을 두면 Cognito가 만든 값이 CloudFormation을 평문으로 지나간다.

**시드 비밀번호**는 소스 상수가 아니라 필수 `NoEcho` 파라미터다 — 상수는 커밋되고, 템플릿과 스택
이벤트에 평문으로 남고, 재배포가 계정을 그 값으로 되돌릴 수 있다. `allowedPattern`이 풀 정책을 배포
시작 전에 검사한다. 없으면 `AdminSetUserPassword`가 몇 분 뒤에 거부하고 스택 전체가 롤백된다. 임시
비밀번호 유효기간은 Cognito 기본 7일이 아니라 30일(`TEMP_PASSWORD_VALIDITY_DAYS`)이라 배포와 워크숍
사이의 간격을 견딘다. 노출 하나가 남는다: `AwsCustomResource` 프로바이더 Lambda가 들어온 이벤트를 한
번 로그에 남긴다. 첫 로그인에서 반드시 바뀌는 값이기 때문에 받아들일 수 있다.

**삭제** — User Pool은 `RemovalPolicy.DESTROY`다. `cdk destroy --all`이 모든 계정을 지운다.

## 오리진 보호

EC2는 80 포트를 CloudFront origin-facing 관리형 프리픽스 리스트에서만 받고, nginx가 CloudFront가
붙이는 비밀 헤더 `X-Origin-Verify`를 검사한다. 두 겹인 이유는 프리픽스 리스트가 "*어떤* CloudFront에서
왔다"만 증명하기 때문이다 — 남의 배포도 그 목록에 있다. SSH 포트는 없고 접속은
`aws ssm start-session`이다.

## 리전 lookup과 cdk.context.json

`CDK_DEPLOY_REGION`이 서울을 덮어쓴다. `PrefixList.fromLookup`이 그 리전의 프리픽스 리스트 ID를 찾으므로
코드 수정은 없지만, 호스팅 스택의 첫 synth에는 계정 자격증명이 필요하다. 결과는 `cdk.context.json`에
캐시되고, 키에 계정 ID가 들어 있어 gitignore되어 있다.

## 테스트가 지키는 것

```bash
npm ci
npm test     # 자격증명 불필요 — 순수 함수 + 합성된 템플릿에 대한 단정
```

| 파일 | 지키는 것 |
|---|---|
| `user-data.assert.ts` | 부팅 스크립트 — nginx 변수와 셸 변수의 이스케이프, 비루트 실행(Claude Code는 euid 0에서 `bypassPermissions`를 거부한다), JWT 쿠키가 들어갈 프록시 버퍼, 서로 다른 두 config dir, `aipds-update` 설치, 서비스 기동 전 `aipds-harden boot` |
| `hosting-stack.assert.ts` | 프리픽스 리스트 전용 SG(SSH 없음), EC2/EBS/EIP/인스턴스 롤, CloudFront 오리진 헤더와 HTTPS 리다이렉트, **앱 클라이언트 드리프트 검사** |
| `auth-stack.assert.ts` | 셀프 사인업 차단, 별칭 username, 그룹, managed login v2, 코드 전용 클라이언트, 시딩 3단계 |
| `auth-client-config.assert.ts` | **프로토타입 빌드 한 번보다 긴** 토큰 유효기간, 시드·그룹 상수, 콜백/로그아웃 URL |
| `preview-stack.assert.ts` | 프로토타입 경로만 오리진에 닿는 것, 두 비밀 헤더, 인스턴스 롤의 프리뷰 시크릿 읽기, 인스턴스 쪽 리소스를 만들지 않는 것 |
| `agent-creds-stack.assert.ts` | `AgentRole`은 인스턴스 롤만 AssumeRole할 수 있고, Bedrock 호출(과 Bedrock을 거친 Marketplace 구독) 말고는 아무것도 못 한다 |
| `sandbox-stacks.assert.ts` | 두 배포 방식 — 새 환경은 참조, 고정하면 **HostingStack에 의존하지 않음** |
| `instance-params.assert.ts` | 스택이 쓰는 SSM 파라미터 이름과 `aipds-harden`이 읽는 이름이 같은 것. 어긋나면 다른 테스트는 다 통과하고, 교체된 인스턴스가 격리를 끈 채 부팅한다 |
| `deployed-tree.assert.ts` | `/opt/aipds`가 되는 트리에 있어야 할 것과 없어야 할 것 |
| `deploy-source.assert.ts` | clone URL이 공개 HTTPS이고, 대상이 고정 커밋이 아니라 브랜치인 것 |
