# Cognito 인증 + 역할 분리 + 사용자 관리 설계

날짜: 2026-07-25
상태: 설계 확정 (사용자 승인)

## 1. 배경과 목표

지금 이 앱에는 인증이 없다. `frontend/lib/auth.ts`의 `getAuthToken()`은 `undefined`를
반환하고, 백엔드 라우트 34개는 전부 무보호다. 최초 스펙
(`2026-07-17-pathfinder-web-service-design.md` §102)이 "SSO는 이후 단계"로 미뤄둔 그
단계를 여기서 처리한다.

목표:

- **Amazon Cognito User Pool + Hosted UI v2 (managed login)** 로 로그인한다.
- 역할은 **`admin`과 `pm`** 두 개. Cognito 그룹 멤버십이 역할의 유일한 출처다.
- **self-signup 차단** — 신규 계정은 관리자 초대로만 생긴다.
- `cdk deploy` 한 번으로 **admin·pm 계정 각 1개가 비밀번호 `PathFinder2026!@`로
  즉시 로그인 가능**한 상태가 된다.
- **`/admin/users` 관리 페이지** — 초대·역할 변경·비밀번호 재설정·비활성화·삭제.

사용자 결정 사항:

- **역할 차이는 사용자 관리 하나뿐** — `admin`은 `pm`의 모든 권한 + `/admin/*`.
  두 역할 모두 모든 프로젝트를 조회·생성·삭제할 수 있다. 프로젝트별 소유권은 만들지
  않는다(현재 데이터 모델에 소유자 개념이 없고, 워크숍 모델은 퍼실리테이터와 고객
  PM의 협업이다).
- **세션은 httpOnly 쿠키 + Next 프록시가 Bearer로 번역**. 토큰은 브라우저 JS에
  노출되지 않는다.
- **초대는 임시 비밀번호를 화면에 1회 표시** — 이메일 발송 없음(SES 도메인 검증·
  샌드박스 해제 불필요).
- **`PATHFINDER_COGNITO_USER_POOL_ID` 미지정 = 인증 바이패스** — 로컬 개발과 기존
  테스트가 Cognito 없이 그대로 돈다.
- **콜백 URL은 배포 마지막에 자동 주입** — CloudFront 도메인을 사람이 옮겨적지 않는다.
- **`/proto/*` 프리뷰는 공개 유지** — 설문 대상자가 계정 없이 프로토타입을 써야 한다.

## 2. 인증 흐름

```
브라우저
  │ ① GET /admin/users  (쿠키 없음)
  ▼
CloudFront ──► nginx ──► Next.js (:3000)
                           │ middleware.ts: pf_access 없음 → 302 /login?next=…
                           ▼
  ② /login → GET /api/auth/login
             · PKCE verifier + state 생성 → httpOnly 쿠키
             · 302 → Hosted UI v2 (managed login)
                           │
                    Cognito User Pool
                     · AllowAdminCreateUserOnly (self-signup 차단)
                     · groups: admin(0), pm(10)
                           │
  ③ 302 ?code=…&state=… → GET /api/auth/callback   (Next route handler, 서버사이드)
             · state 쿠키 일치 검증
             · POST /oauth2/token  (client_secret + code_verifier)
             · pf_access / pf_id / pf_refresh → httpOnly·Secure·SameSite=Lax 쿠키
             · 302 → next (기본 "/")
                           │
  ④ 이후 모든 API 호출: 프론트는 same-origin /api/* 만 부른다
     /api/[...path] 프록시: pf_access 쿠키 → Authorization: Bearer 로 번역
                           ▼
                      FastAPI (:8000)
                       · JWKS 캐시로 RS256 서명 검증
                       · iss / aud(client_id) / exp / token_use=="access"
                       · cognito:groups → role
                       · require_user / require_admin
```

**토큰을 JS에 노출하지 않는 이유**: 코드 교환을 서버사이드에서 하고 httpOnly 쿠키에만
담으면 XSS가 토큰을 훔칠 수 없다. 대가는 프록시 경유 의존성인데, 배포 환경은 이미
`NEXT_PUBLIC_API_BASE_URL=/api`로 빌드된다(`infra/lib/user-data.ts:56`).

**SSE가 공짜로 해결된다**: `EventSource`는 커스텀 헤더를 못 보내지만 same-origin 쿠키는
자동으로 보낸다. `?token=` 쿼리 파라미터(로그·리퍼러 유출 경로)가 필요 없다.

**로컬 dev의 한계**: 프론트가 `http://localhost:8000`을 직접 부르는 모드에서는 쿠키가
cross-origin이 되어 인증이 성립하지 않는다. 그래서 로컬은 §6의 바이패스 모드로 돈다.
인증을 켠 채로 로컬 검증이 필요하면 `NEXT_PUBLIC_API_BASE_URL=/api`로 띄운다.

**리프레시**: `/api` 프록시가 백엔드 401을 받으면 `pf_refresh`로 `refresh_token` 그랜트를
한 번 시도하고, 성공하면 쿠키를 갱신해 원 요청을 재시도한다. 실패하면 401을 그대로
흘리고 프론트가 `/login`으로 보낸다. access/id 1시간, refresh 30일.

**재시도에서 제외되는 요청**: 요청 본문이 스트림인 호출(SSE 및 body를 재생할 수 없는
요청)은 재시도하지 않는다. 스트림은 한 번 소비되면 되돌릴 수 없다.

## 3. 인프라 — `PathfinderAuthStack`

새 파일 `infra/lib/pathfinder-auth-stack.ts`. `bin/app.ts`는 drill → auth → hosting
순으로 엮는다.

### 3.1 User Pool

| 설정 | 값 | 이유 |
|---|---|---|
| `selfSignUpEnabled` | `false` | CFN `AllowAdminCreateUserOnly: true`. self-signup 차단의 실체이고 Hosted UI에 가입 링크가 렌더되지 않는다 |
| `signInAliases` | `{ username: true, email: true }` | → `AliasAttributes: [email]`. 이메일로 로그인하되 `Username`은 **호출자가 지정**한다. `{ email: true }`만 두면 Cognito가 username을 UUID로 자동 생성해 시딩·관리 API가 비결정적이 된다(§5.2) |
| `passwordPolicy` | 8자+, 대/소/숫자/기호 | `PathFinder2026!@`가 통과하는 최소 정책 |
| `mfa` | `OFF` | 워크숍 환경 |
| `accountRecovery` | `NONE` | 이 앱은 메일을 전혀 보내지 않으므로(§1 결정) 자가 재설정 코드를 전달할 경로가 없다. 재설정은 관리 페이지에서 관리자가 한다 |
| `removalPolicy` | `DESTROY` | 버킷과 동일 정책 — `cdk destroy` 시 사용자도 함께 사라진다 |

### 3.2 그룹

`CfnUserPoolGroup` 2개: `admin`(precedence 0), `pm`(precedence 10). 커스텀 속성으로
role을 두지 않는다 — 진실이 두 곳에 생기면 어긋난다.

### 3.3 Hosted UI v2

- `UserPoolDomain` + `managedLoginVersion: ManagedLoginVersion.NEWER_MANAGED_LOGIN` (=2).
- `CfnManagedLoginBranding` + `useCognitoProvidedValues: true`. v2는 브랜딩 스타일
  레코드가 있어야 정상 렌더되므로, 콘솔이 자동으로 하는 일을 CFN에 명시한다.
- 도메인 프리픽스 `pathfinder-${account}-${region}` — 계정·리전 안에서 유일해야 한다.

### 3.4 앱 클라이언트

confidential 클라이언트(`generateSecret: true`) + `authorizationCodeGrant` + PKCE.
scope는 `openid email profile`. 토큰 유효기간 access/id 1h, refresh 30d.

시크릿을 두는 이유: 코드 교환이 서버사이드(Next route handler)이므로 시크릿을 안전하게
보관할 수 있고, 두면 client_id만 아는 공격자가 가로챈 코드를 교환할 수 없다.

**시크릿 값 전달** — 템플릿에 평문을 남기지 않기 위해 EC2가 **부팅 시 직접 조회**한다:
`aws cognito-idp describe-user-pool-client --query UserPoolClient.ClientSecret`. 인스턴스
롤에 `cognito-idp:DescribeUserPoolClient`(해당 풀 ARN만)를 부여한다.

Secrets Manager 사본을 두지 않는 이유: 시크릿은 Cognito가 생성하므로 사본을 만들려면
값을 CFN 경유로 옮겨야 하고(=템플릿에 남는다) 커스텀 리소스가 하나 더 필요하다.
`X-Origin-Verify`는 CDK가 값을 만들었기 때문에 Secrets Manager가 맞았지만, 여기서는
Cognito가 원본 보관소이므로 부팅 시 조회가 더 짧고 안전하다.

### 3.5 콜백 URL 순환 의존 해소

AuthStack은 `http://localhost:3000/api/auth/callback`만 등록한다. HostingStack이
CloudFront를 만든 뒤 `AwsCustomResource`로 `UpdateUserPoolClient`를 한 번 호출해
`https://{distributionDomain}/api/auth/callback`을 덧붙인다.

⚠️ **`UpdateUserPoolClient`는 PUT 시맨틱이다** — 지정하지 않은 필드를 지운다. 따라서
이 호출은 "기존 목록에 추가"가 아니라 **CDK가 아는 전체 설정(콜백·로그아웃 URL·
그랜트·scope·토큰 유효기간)을 그대로 다시 쓴다**. 이 구현의 유일하게 까다로운 지점이며,
AuthStack의 클라이언트 설정과 이 커스텀 리소스의 파라미터는 한 곳(`lib/auth-client-config.ts`)
에서 도출해 어긋나지 않게 한다.

로그아웃 URL도 같은 방식으로 `https://{domain}/login`을 등록한다.

CloudFront 도메인은 배포마다 바뀌지 않으므로(distribution을 지우지 않는 한) 이 커스텀
리소스는 재배포 시 no-op이다. distribution이 교체되면 새 도메인으로 다시 쓴다.

### 3.6 CfnOutputs

`UserPoolId`, `UserPoolClientId`, `HostedUiDomain`.

클라이언트 시크릿은 출력하지 않는다(§3.4 — EC2가 부팅 시 조회한다).

## 4. 계정 시딩

`infra/lib/seed-users.ts`에 헬퍼 `seedUser(scope, {userPool, username, group, password})`.
AuthStack에서 두 번 호출한다. 각 호출은 `AwsCustomResource` 3개를 순서대로 엮는다:

1. **`AdminCreateUser`** — `Username: <이메일>`, `MessageAction: SUPPRESS`(메일 발송 없음),
   `UserAttributes: [{email}, {email_verified: "true"}]`. 재배포 시
   `UsernameExistsException`은 `ignoreErrorCodesMatching`으로 무시한다.
2. **`AdminSetUserPassword`** — `Password: 'PathFinder2026!@'`, `Permanent: true`.
   상태가 `CONFIRMED`가 되어 **첫 로그인에서 비밀번호 변경을 요구하지 않는다.**
   `onUpdate`에도 걸어 재배포마다 비밀번호를 다시 확정한다 — 누가 바꿔놨어도 배포하면
   알려진 값으로 돌아온다(데모 환경에서 원하는 성질).
3. **`AdminAddUserToGroup`** — `admin` 또는 `pm`.

2·3단계는 1단계 응답을 참조하지 **않고** 같은 이메일 상수를 `Username`으로 직접 넘긴다.
§3.1이 username을 호출자 지정으로 두는 이유가 이것이다 — 1단계가 재배포 시
`UsernameExistsException`으로 무시되어 응답 필드가 비어도 2·3단계가 깨지지 않는다
(`AwsCustomResource`에는 조건 분기가 없으므로 단계 간 값 전달에 의존하면 취약해진다).

각 단계는 `AwsCustomResource`의 `physicalResourceId`를 이메일+단계명으로 고정해
재배포 시 교체가 아닌 갱신으로 처리된다.

계정: `admin@pathfinder.local`(그룹 admin), `pm@pathfinder.local`(그룹 pm).
실제로 메일을 보내지 않으므로 `.local`이 오히려 시드 계정임을 드러낸다. 상수 2개만
바꾸면 다른 주소로 바뀐다.

### 4.1 명시적 트레이드오프 — 시드 비밀번호는 템플릿에 평문으로 남는다

`PathFinder2026!@`는 CDK 소스의 상수이므로 **CloudFormation 템플릿과 스택 이벤트에
평문으로 남는다.** 계정에 CFN 읽기 권한이 있는 사람은 누구나 볼 수 있다.

요청대로 사전 설정으로 구현하되, 이것은 데모/워크숍용 자격증명이며 운영 전환 시 반드시
교체해야 한다 — README와 `infra/README.md`에 명시한다. `NoEcho` 파라미터로 가리는
대안은 `cdk deploy`마다 값을 넘겨야 해서 "한 번에 배포" 요구와 충돌하므로 택하지 않았다.

## 5. 백엔드

### 5.1 새 모듈

```
backend/pathfinder/auth/
├── __init__.py
├── verifier.py     # JWKS 캐시 + JWT 검증 → Principal
└── deps.py         # require_user / require_admin
backend/pathfinder/routes/
├── admin_users.py  # /admin/users*  (전부 require_admin)
└── proto_public.py # /proto/*       (공개 — prototypes.py에서 분리)
```

**`verifier.py`** — JWKS를 `httpx`로 받아 `kid`→키로 캐시하고, RS256 서명·`iss`·
`exp`·`token_use=="access"`·`client_id`를 검증해 `Principal(username, sub, role)`을 낸다.
`kid` 미스일 때만 JWKS를 재조회한다(키 로테이션 대응). 검증 실패는 401.

⚠️ **access 토큰은 `aud`가 아니라 `client_id`로 앱 클라이언트를 식별한다**
([Verifying JSON web tokens](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html):
"The `aud` claim in an ID token and the `client_id` claim in an access token must match
the app client ID"). 따라서 PyJWT는 `options={"verify_aud": False}`로 호출하고
`client_id`를 코드에서 직접 비교한다. `audience=`로 넘기면 검증이 실패한다.

⚠️ **access 토큰에는 `email`이 없다** — 기본 payload는 `sub`·`cognito:groups`·`iss`·
`client_id`·`token_use`·`scope`·`exp`·`username`이다
([Understanding the access token](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-access-token.html)).
그래서 `Principal`은 email을 담지 않는다. 화면에 표시할 이메일은 프론트가 **id 토큰**
(`pf_id` 쿠키)에서 읽는다(§6.2 `/api/auth/me`).

서명 검증은 직접 구현하지 않고 **`PyJWT[crypto]`** 를 의존성에 추가한다 — 암호 코드를
직접 쓰는 것은 이 프로젝트에서 가장 피하고 싶은 일이다. (venv에 이미 PyJWT 2.13.0 +
cryptography 49.0.0이 전이 의존성으로 들어와 있으나, 전이 의존성에 기대지 않고
`pyproject.toml`에 명시한다.)

**`deps.py`** — `require_user`(admin·pm 통과), `require_admin`(admin만, 아니면 403).

### 5.2 관리 API — 전부 `require_admin`

| 라우트 | 동작 |
|---|---|
| `GET /admin/users` | `ListUsers` — 이메일·상태·활성여부·생성일 + 사용자별 `AdminListGroupsForUser` |
| `POST /admin/users` | 초대: `AdminCreateUser`(SUPPRESS, 이메일은 `UserAttributes`) → `AdminSetUserPassword(Permanent=false)` → `AdminAddUserToGroup`. 임시 비밀번호는 **백엔드가 생성**해 응답 본문에 **1회만** 반환하고 어디에도 저장하지 않는다 |
| `POST /admin/users/{u}/reset-password` | 새 임시 비밀번호 생성 → 동일 방식, 1회 반환 |
| `PUT /admin/users/{u}/role` | 그룹 교체(제거 후 추가) |
| `POST /admin/users/{u}/disable` · `/enable` | `AdminDisableUser` / `AdminEnableUser` |
| `DELETE /admin/users/{u}` | `AdminDeleteUser` |

`{u}`는 Cognito `Username`이다. §3.1의 풀 설정
(`signInAliases: { username: true, email: true }` → `AliasAttributes: [email]`)에서는
**호출자가 `Username`을 지정**하므로, 초대 API는 이메일을 `Username`으로 넘기고 동시에
`UserAttributes`의 `email`로도 넘긴다(alias 등록에 필요). 결과적으로 username == email이다.

이 설정을 택한 이유는 §4의 시딩 결정성 때문이다 — `UsernameAttributes: [email]`(username
사인인 미포함)이면 Cognito가 username을 UUID로 자동 생성하고
([admin-create-user](https://docs.aws.amazon.com/cli/latest/reference/cognito-idp/admin-create-user.html):
"Amazon Cognito automatically generates a username value"), 그 값을 CDK 커스텀 리소스가
재배포마다 안정적으로 알 수 없다.

- `GET /admin/users`는 `username`과 `email`을 **둘 다** 반환한다. 화면은 email을
  보여주고 액션은 username을 보낸다 — 두 값이 지금은 같더라도 화면이 그 등식에
  의존하지 않게 한다.
- 프론트는 username을 조립하지 않는다 — 목록이 준 값을 그대로 되돌려보낸다.
- **`email_verified: true`가 필수다** — alias 사인인은 검증된 이메일에만 동작한다.
- 이메일은 풀 안에서 유일해야 한다(alias 제약). 중복 초대는 Cognito가
  `AliasExistsException`으로 거부하고, 라우트는 409로 변환한다.

**임시 비밀번호 생성** — `secrets.choice`로 각 문자군(대/소/숫자/기호) 최소 1개를
보장한 16자.

**초대의 부분 실패 처리** — 순서는 create → set password → add group. 중간 단계가
실패하면 방금 만든 사용자를 지우고 500을 낸다. 반쯤 만들어진 계정(그룹 없음 = 역할
없음)을 남기지 않는다 — `routes/projects.py:56`의 매니페스트 실패 롤백과 같은 규율.

**마지막 관리자 보호** — 다음을 400으로 거부한다:

1. 자기 자신의 역할 강등 / 비활성화 / 삭제
2. admin 그룹 멤버가 1명뿐일 때 그 계정의 강등 / 비활성화 / 삭제

이게 없으면 관리 페이지에서 스스로를 잠가내고 복구 경로가 AWS 콘솔밖에 남지 않는다.

### 5.3 전역 적용과 공개 예외

`app.py`에서 라우터를 include할 때 인증이 필요한 라우터에
`dependencies=[Depends(require_user)]`를 붙인다. 공개로 남는 것은 두 개다:

- `surveys_public` — 익명 설문 응답(`/survey/{token}`)
- `proto_public` — 프로토타입 라이브 프리뷰(`/proto/{pid}/{slug}/*`)

`/proto/*` 두 라우트는 현재 `prototypes.py`(인증 필요 라우트 9개와 같은 파일) 안에
있으므로 `proto_public.py`로 분리한다. **파일 경계가 곧 인증 경계**가 되어 나중에
실수로 공개 라우트를 늘리기 어려워진다.

**공개 프리뷰의 한계를 명시한다**: `/proto/*`는 `pid`와 `slug`를 아는 사람이면 누구나
접근할 수 있고, 이는 현상 유지(지금도 공개)다. 방어선은 slug의 추측 난이도뿐인
얕은 보안이다. 설문 대상자가 계정 없이 프로토타입을 써야 하므로 이 선택은 의도적이며,
민감 데이터를 프로토타입에 넣지 않는 것이 전제다.

### 5.4 로컬 바이패스

`PATHFINDER_COGNITO_USER_POOL_ID`가 비어 있으면 `require_user`/`require_admin`이
`Principal(username="local-dev", email="local-dev", role="admin")`을 즉시 반환한다.

기존 `durable_projects_enabled()`(버킷 env 없으면 S3 생략) 패턴과 결이 같다. EC2
systemd 유닛은 항상 이 env를 심으므로 프로덕션에서 바이패스가 켜질 수 없다.

효과: 기존 pytest 53개 파일이 무수정으로 통과하고, 로컬 실행 절차도 변함이 없다.
인증 자체의 테스트는 env를 세팅한 상태에서 가짜 JWKS/서명키로 검증한다.

### 5.5 새 환경 변수

| 변수 | 설명 |
|---|---|
| `PATHFINDER_COGNITO_USER_POOL_ID` | 미지정 시 인증 바이패스(로컬/테스트) |
| `PATHFINDER_COGNITO_CLIENT_ID` | access 토큰 `client_id` 클레임 검증용 (§5.1 — `aud` 아님) |
| `PATHFINDER_COGNITO_REGION` | 기본 `PATHFINDER_S3_REGION`과 동일 |

## 6. 프론트엔드

### 6.1 미들웨어

`frontend/middleware.ts` — `pf_access` 쿠키가 없으면 302 `/login?next=…`.
`matcher`에서 제외: `/api/auth/*`, `/survey/*`, `/proto/*`, `/_next/*`, `/login`.

`/admin/*`은 쿠키 안 JWT의 `cognito:groups`를 **서명 검증 없이** 읽어 admin이 아니면
`/`로 보낸다. ⚠️ 미들웨어는 **UX 게이트이고 보안 경계가 아니다** — 실제 방어선은
백엔드의 `require_admin`이다. 이 점을 코드 주석에 남긴다(미들웨어를 보안 경계로
착각하는 것이 이 패턴의 전형적 사고).

### 6.2 auth route handlers

`frontend/app/api/auth/`:

| 경로 | 동작 |
|---|---|
| `login/route.ts` | PKCE verifier + state 생성 → httpOnly 쿠키 → Hosted UI 302 |
| `callback/route.ts` | state 검증 → `/oauth2/token` 교환 → 쿠키 3개 세팅 → `next`로 302 |
| `logout/route.ts` | 쿠키 삭제 → Cognito `/logout` 302 |
| `me/route.ts` | `pf_id`(id 토큰)에서 email, `pf_access`에서 `cognito:groups` → role 추출. 클라이언트가 사용자 표시용으로 부르는 유일한 경로. email이 access 토큰에 없기 때문에 id 토큰이 필요하다(§5.1) |

쿠키 속성: `httpOnly`, `secure`(프로덕션), `sameSite: "lax"`, `path: "/"`.
`sameSite: lax`인 이유 — Hosted UI에서 돌아오는 top-level 리다이렉트에 쿠키가 실려야
한다(`strict`면 콜백 직후 요청에서 쿠키가 빠진다).

### 6.3 `/api/[...path]` 프록시 변경

`pf_access` 쿠키를 읽어 `Authorization: Bearer`를 붙인다. `Cookie` 헤더는 백엔드로
넘기지 않는다(백엔드는 쿠키를 모른다). 401이면 §2의 리프레시-재시도를 한 번 한다.

### 6.4 `lib/auth.ts` 대체

`getAuthToken()`을 삭제하고 호출부 3곳(`lib/api/client.ts`, `lib/api/http.ts`,
`lib/api/prototypes.ts`)을 `credentials: "include"`로 바꾼다. `X-Project-Token` 헤더는
소멸한다.

### 6.5 화면

**`/login`** — Hosted UI로 보내는 버튼 하나. `?error=`가 있으면 한국어로 사유 표시.

**`/admin/users`** — 사용자 표(이메일 · 역할 배지 · 상태 · 생성일).
- "사용자 초대" 모달: 이메일 + 역할 선택 → 생성 성공 시 임시 비밀번호를 **복사 버튼과
  함께 1회만** 표시하고 "이 창을 닫으면 다시 볼 수 없습니다" 경고.
- 행별 액션: 역할 변경 · 비밀번호 재설정 · 비활성/활성 · 삭제(확인 모달).
- 서버가 400으로 거부한 마지막-관리자 보호는 그 메시지를 그대로 표시한다.

**`AppHeader.tsx`** — 하드코딩된 "김PM" 버튼(`AppHeader.tsx:63`)을 실제 사용자
이니셜 + 드롭다운으로 교체: 이메일·역할 표시 / (admin이면) "사용자 관리" 링크 / 로그아웃.

### 6.6 새 환경 변수

| 변수 | 설명 |
|---|---|
| `COGNITO_HOSTED_UI_DOMAIN` | 예 `pathfinder-123456789012-ap-northeast-2.auth.ap-northeast-2.amazoncognito.com` |
| `COGNITO_CLIENT_ID` | 앱 클라이언트 ID |
| `COGNITO_CLIENT_SECRET` | 토큰 교환용 (server-side only, `NEXT_PUBLIC_` 아님) |
| `APP_BASE_URL` | 콜백 URL 조립용 (예 `https://d….cloudfront.net`) |

`user-data.ts`의 프론트 systemd 유닛에 이 4개를 심고, `COGNITO_CLIENT_SECRET`은 부팅 시
`describe-user-pool-client`로 조회한다(§3.4).

⚠️ 이 값들은 **서버사이드 전용**이다 — `NEXT_PUBLIC_` 접두어를 붙이면 클라이언트
번들에 인라인되어 시크릿이 브라우저로 나간다. 인증 정보는 route handler와 미들웨어에서만
읽는다.

## 7. 오류 처리

| 상황 | 동작 |
|---|---|
| 쿠키 없음 | 미들웨어 302 `/login?next=…` |
| 토큰 만료 | 프록시가 refresh 1회 → 실패 시 401 → 프론트가 `/login` |
| SSE 중 401 | 스트림 훅이 `/login`으로 이동(재시도 안 함 — §2) |
| pm이 `/admin/*` API 호출 | 백엔드 403 → 프론트 "권한이 없습니다" |
| JWKS 조회 실패 | 401 + 서버 로그 `exception` (fail-closed) |
| state 불일치 | `/login?error=state_mismatch` |
| 중복 이메일 초대 | `AliasExistsException`/`UsernameExistsException` → 409 "이미 등록된 이메일입니다" |
| Cognito API 실패 | 502 + 원 오류 코드는 로그에만 (사용자에게 내부 세부사항 노출 안 함) |
| 초대 부분 실패 | 방금 만든 사용자 삭제 후 500 (§5.2) |

## 8. 테스트

**백엔드** (Cognito는 botocore Stubber, JWT는 테스트용 RSA 키로 서명)
- `verifier.py`: 유효 토큰 / 만료 / 잘못된 `client_id` / 잘못된 `iss` / `kid` 미스 후 재조회 / `token_use=="id"` 거부 / `cognito:groups`에 admin·pm이 없을 때 거부
- `deps.py`: 바이패스 모드, pm이 `require_admin`에서 403
- 관리 라우트 7개 각각의 정상 경로
- 마지막 관리자 보호 3케이스(자기 강등 · 자기 삭제 · 유일 admin 비활성)
- 초대 부분 실패 시 롤백
- 공개 라우트(`/survey/{token}`, `/proto/*`)가 인증 없이 통과

**프론트엔드** (Vitest + MSW)
- 미들웨어: 쿠키 없음 → 리다이렉트, `/survey` 통과, pm의 `/admin` 차단
- `callback` handler: state 불일치 거부, 쿠키 속성(httpOnly/secure/sameSite)
- 프록시: Bearer 주입, 401 → refresh → 재시도, 스트림 요청은 재시도 안 함
- `/admin/users`: 렌더, 초대 흐름, 임시 비밀번호 1회 표시, 삭제 확인

**인프라** (`cdk synth` 후 템플릿 단정 — 기존 `test/*.assert.ts` 패턴)
- `AllowAdminCreateUserOnly: true`
- `AliasAttributes: ['email']` (그리고 `UsernameAttributes` 부재 — §3.1)
- 그룹 2개(admin/pm)와 precedence
- `ManagedLoginVersion: 2` + 브랜딩 리소스
- 시드 커스텀 리소스 6개(계정 2 × 단계 3)
- 클라이언트가 `authorizationCodeGrant`만 허용

**수동 e2e 체크리스트** (`docs/superpowers/checklists/`)
1. `npx cdk deploy --all` → CloudFront 도메인 접속 → `/login` 리다이렉트 확인
2. `admin@pathfinder.local` / `PathFinder2026!@` 로그인 → 비밀번호 변경 요구 없음 확인
3. `pm@pathfinder.local` 로그인 → `/admin/users` 접근 차단 확인
4. admin으로 신규 사용자 초대 → 임시 비밀번호 확보 → 그 계정으로 첫 로그인(변경 요구됨)
5. Hosted UI에 회원가입 링크가 없음 확인
6. `/survey/{token}`을 로그아웃 상태에서 열어 응답 가능 확인
7. 캔버스에서 메시지 전송 → SSE가 쿠키 인증으로 흐르는지 확인

## 9. 범위 밖

- MFA, 소셜 IdP / SAML
- 비밀번호 자가 재설정 메일(SES) — 관리자 재설정으로 대체
- 프로젝트별 소유권·멤버십 (§1 결정: 두 역할 모두 전 프로젝트 접근)
- 감사 로그(누가 누구를 초대·삭제했는지)
- 커스텀 도메인 + ACM (CloudFront 기본 도메인 사용)
- Hosted UI 브랜딩 커스터마이즈 (Cognito 제공 기본값 사용)

## 10. 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `infra/lib/pathfinder-auth-stack.ts` | 신규 — User Pool · 그룹 · Hosted UI v2 · 클라이언트 |
| `infra/lib/seed-users.ts` | 신규 — 시드 계정 헬퍼 |
| `infra/lib/auth-client-config.ts` | 신규 — 클라이언트 설정 단일 출처(§3.5) |
| `infra/bin/app.ts` | AuthStack 추가, hosting에 auth 참조 전달 |
| `infra/lib/pathfinder-hosting-stack.ts` | 콜백 URL 주입 커스텀 리소스 + 프론트 env |
| `infra/lib/user-data.ts` | 프론트 systemd에 Cognito env 4개, 백엔드에 3개 |
| `infra/test/auth-stack.assert.ts` | 신규 — 템플릿 단정 |
| `backend/pathfinder/auth/{verifier,deps}.py` | 신규 |
| `backend/pathfinder/routes/admin_users.py` | 신규 |
| `backend/pathfinder/routes/proto_public.py` | 신규 — `prototypes.py`에서 `/proto/*` 분리 |
| `backend/pathfinder/app.py` | 라우터 include에 인증 의존성, 공개 2개 예외 |
| `backend/pyproject.toml` | `PyJWT[crypto]` 추가 |
| `frontend/middleware.ts` | 신규 |
| `frontend/app/api/auth/{login,callback,logout,me}/route.ts` | 신규 |
| `frontend/app/login/page.tsx` | 신규 |
| `frontend/app/admin/users/page.tsx` | 신규 |
| `frontend/components/admin/*` | 신규 — 사용자 표 · 초대 모달 |
| `frontend/app/api/[...path]/route.ts` | Bearer 주입 + 401 리프레시 |
| `frontend/lib/auth.ts` | `getAuthToken()` 삭제 → `credentials: "include"` |
| `frontend/lib/api/{client,http,prototypes}.ts` | 호출부 수정 |
| `frontend/components/AppHeader.tsx` | 하드코딩 "김PM" → 실제 사용자 메뉴 |
| `README.md` · `infra/README.md` | 인증 절차 · env · 시드 계정 경고 |
