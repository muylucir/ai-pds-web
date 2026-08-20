# Pathfinder Infra (CDK, 기본 ap-northeast-2 / 서울)

**한국어** | [English](README.md)

배포 절차 — 부트스트랩, `cdk deploy`, 출력값, 접속, 리전 변경, 코드 갱신, 정리,
트러블슈팅 — 는 루트 [`README.ko.md`](../README.ko.md)에 있다. 이 문서는 **스택이 왜
이 모양인지**를 다룬다: 사람이 놓치면 에러 없이 조용히 깨지는 판단들이다.

## 세 스택

| 스택 | 만드는 것 |
|---|---|
| `PathfinderDrillStack` | S3 아티팩트 버킷(`projects/*` + `sessions/*` + `surveys/*` + `models/*`) + 백엔드 실행 롤(Bedrock invoke + S3) |
| `PathfinderAuthStack` | Cognito User Pool + Hosted UI v2(managed login) + 역할 그룹 2개(`admin`/`pm`) + 시드 계정 2개 |
| `PathfinderHostingStack` | VPC + EC2(AL2023 x86_64, m7i.2xlarge, 100 GB 암호화 EBS) + CloudFront |

세 스택은 서로를 참조하므로 **`--all`로 함께 배포한다**(`bin/app.ts`가 버킷과 User
Pool 참조를 호스팅 스택에 넘긴다). 순서는 CDK가 정한다.

**버킷 프리픽스가 네 개인 이유**는 `lib/backend-permissions.ts`에 있다. 각각
프로젝트 데이터·세션 트랜스크립트·설문·모델 카탈로그이고, 뒤의 둘은 프로젝트
프리픽스 **밖**이어야 한다(설문 토큰은 어느 프로젝트 것인지 모르는 상태에서
조회되고, 모델 카탈로그는 프로젝트가 하나도 없을 때 읽힌다). 이 목록에서
`surveys/*`가 빠져 설문 생성이 전부 500이었던 실측 버그가 그 주석의 근거다 —
증상은 화면의 일반 오류였고 원인은 백엔드 로그의 `AccessDenied` 한 줄이었다.

프로토타입 빌드(Claude Agent SDK 에이전트)는 **백엔드 프로세스 안에서** 직접
돌아간다 — 별도 VM/MicroVM 계층이 없다.

## 배포되는 코드: 워킹 트리가 아니라 푸시된 main

user-data가 공개 리포를 clone해 부팅 시점의 `origin/main` 최신 커밋으로 맞춘 뒤
백엔드/프론트를 빌드·기동한다. 근거는 `lib/deploy-source.ts`에 길게 적혀 있고 요지는
두 가지다.

- **clone은 tracked 파일만 가져온다.** 종전 CDK 에셋(zip) 방식은 gitignore된 파일까지
  실었고, 그것을 사람이 관리하는 제외 목록으로 보정해야 했다. 그 목록에서 빠진 것이
  두 번 사고를 냈다(개발용 `.claude/CLAUDE.md`가 에이전트 cwd의 **조상**으로 들어가
  영어 프로젝트에 한국어 한 줄을 주입한 것, 개발 박스의 `proto-type/`이 실려 아무도
  빌드하지 않은 프로토타입이 "빌드 완료"로 보인 것). `test/deployed-tree.assert.ts`가
  `git ls-files`로 그 불변식을 고정한다.
- **커밋 SHA를 고정하지 않는다.** 그래서 배포자에게 "이 커밋을 푸시했는가"를 묻지
  않지만, 대가로 **`cdk deploy`가 코드 갱신 수단이 아니다** — user-data 문자열이
  바이트 단위로 같으면 CloudFormation이 인스턴스를 교체하지 않는다. 코드 갱신은
  부팅 시 설치되는 `pathfinder-update`가 담당한다(루트 README의 "코드 갱신" 절).

## PathfinderAuthStack

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
"콜백 URL 순환 의존"). 둘이 어긋나면 재배포마다 유효기간·인증 플로우가 조용히
리셋된다.

### 콜백 URL 순환 의존

Cognito는 콜백 URL의 전수 일치만 허용하고(와일드카드 불가) 실제 URL은
HostingStack이 만드는 CloudFront 도메인에 달려 있다. AuthStack은 localhost 콜백만
갖고 배포되고, HostingStack이 배포 마지막에 `UpdateUserPoolClient`로 실제 도메인을
등록한다.

⚠️ **그 API는 PUT 시맨틱이다** — 지정하지 않은 필드를 지운다. 따라서 콜백만 보내는
것이 아니라 클라이언트 설정 전체(콜백/로그아웃 URL, OAuth 스코프, 토큰 유효기간,
auth flow)를 다시 쓴다. 값의 출처가 `lib/auth-client-config.ts` 하나뿐이라
AuthStack과 어긋나지 않는다. **AuthStack의 앱 클라이언트에 필드를 추가하면
HostingStack의 재전송에도 반드시 그 필드를 미러링해야 한다** — 누락하면 재배포 시
그 필드가 조용히 지워진다. 사람이 놓쳐도 CI가 잡도록 드리프트 감지 테스트
(`test/hosting-stack.assert.ts`)가 두 정의를 비교한다.

### 클라이언트 시크릿

CfnOutput으로 내보내지 않는다. EC2가 부팅 시
`aws cognito-idp describe-user-pool-client`로 직접 읽는다 — Secrets Manager 사본을
만들려면 Cognito가 생성한 값을 CFN 경유로 옮겨야 하고, 그러면 템플릿에 평문으로
남는다. 대가는 인스턴스 롤의 `cognito-idp:DescribeUserPoolClient` 권한이다.

### 시드 비밀번호 경고

`SEED_PASSWORD`(`AiPdsWeb2026@!`)는 `lib/auth-client-config.ts`의 상수이므로
**CloudFormation 템플릿과 스택 이벤트에 평문으로 남고, 재배포는 이 값으로
되돌린다.** 데모/워크숍 전용이며 운영 전환 시 반드시 교체하고 `/admin/users`에서
초대한 계정을 쓴다. `NoEcho` 파라미터로 가리는 대안은 `cdk deploy`마다 값을 넘겨야
해서 "한 번에 배포" 요구와 충돌하므로 택하지 않았다.

### 삭제

`cdk destroy --all` 시 User Pool은 `RemovalPolicy.DESTROY`이므로 **사용자 전원이
함께 사라진다.**

## 오리진 보호

EC2는 CloudFront origin-facing 관리형 프리픽스 리스트(배포 리전 자동 조회)에서만
80을 받고, CloudFront가 붙이는 비밀 헤더 `X-Origin-Verify`를 nginx가 검증한다. SSH
포트는 열지 않는다 — 접속은 `aws ssm start-session`이다. 두 겹인 이유는 프리픽스
리스트가 "CloudFront에서 온 트래픽"까지만 좁혀 주기 때문이다: **다른 사람의**
CloudFront 배포도 그 목록에 들어가므로, 우리 배포인지는 헤더로만 구별된다.

## 리전 lookup과 cdk.context.json

기본 서울(`ap-northeast-2`), `CDK_DEPLOY_REGION`으로 오버라이드한다. 프리픽스 리스트
ID는 리전마다 다르지만 `PrefixList.fromLookup`이 배포 리전의 ID를 자동 조회하므로
코드 수정이 필요 없다. 대가는 **호스팅 스택의 첫 synth/deploy에 계정 크리덴셜이
필요**하다는 것이다(`npx cdk synth PathfinderDrillStack`은 필요 없다).

조회 결과는 `cdk.context.json`에 캐시되지만 **커밋하지 않는다**(gitignored) — 항목
키에 계정 ID가 들어가 다른 계정에서는 무효인 캐시이고, 크리덴셜이 있으면 같은 값으로
재생성된다. 그래서 클론당 첫 synth 1회는 크리덴셜이 필요하다.

## 테스트가 지키는 것

```bash
npm ci
npm test     # 크리덴셜 불필요 — 순수함수 + 합성된 템플릿 단정
```

여섯 개 어서션 파일이고, 각자 **눈으로는 안 보이는** 회귀를 겨냥한다:

| 파일 | 지키는 것 |
|---|---|
| `user-data.assert.ts` | 부팅 스크립트의 요소 전부 — nginx 변수 vs 셸 변수 이스케이프, non-root 실행(Claude Code는 euid 0에서 `bypassPermissions`를 거부한다), JWT 쿠키가 들어가는 프록시 버퍼 크기, 두 config dir이 서로 다른 경로인지, 컨텍스트 스위치 두 개, `pathfinder-update` 설치 |
| `hosting-stack.assert.ts` | SG가 프리픽스 리스트 전용인지(SSH 없음), EC2/EBS/EIP/인스턴스 롤, CloudFront의 오리진 헤더와 HTTPS 리다이렉트, 그리고 위의 **앱 클라이언트 드리프트 감지** |
| `auth-stack.assert.ts` | self-signup 차단·alias username·그룹·managed login v2·code-only 클라이언트, 시드 계정 3단계의 짝 맞춤 |
| `auth-client-config.assert.ts` | 토큰 유효기간이 **프로토타입 빌드 1회보다 길다**(짧으면 빌드 중 세션이 만료된다), 시드/그룹 상수와 콜백·로그아웃 URL 파생 |
| `deployed-tree.assert.ts` | `/opt/pathfinder`가 될 트리에 있으면 안 되는 것(개발용 `.claude/`, 빌드 산출물, 세션 상태)과 있어야 하는 것(룰, 두 언어 지시, 두 config dir, 락파일) |
| `deploy-source.assert.ts` | clone URL이 공개 HTTPS인지, 배포 대상이 커밋이 아니라 브랜치인지 |

## PathfinderVmStack은 제거됐다 (2026-07-25)

프로토타입 빌드가 백엔드 프로세스 안에서 돌게 되면서 도쿄 MicroVM·이미지 빌드·토큰
민팅이 모두 사라졌다. 이전에 배포한 적이 있다면 한 번 정리한다:

```bash
npx cdk destroy PathfinderVmStack --region ap-northeast-1
```
