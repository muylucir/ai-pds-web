# Cognito 인증 + 역할 분리 + 사용자 관리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cognito User Pool + Hosted UI v2로 로그인하고, `admin`/`pm` 두 역할을 분리하고,
self-signup을 막고, `cdk deploy` 한 번으로 로그인 가능한 시드 계정 2개를 만들고,
`/admin/users` 관리 페이지에서 초대·역할변경·비밀번호재설정·비활성화·삭제를 한다.

**Architecture:** 브라우저는 Hosted UI v2로 로그인하고, Next route handler가
서버사이드에서 authorization code(PKCE)를 토큰으로 교환해 **httpOnly 쿠키**에만 담는다.
모든 API 호출은 same-origin `/api/*` 프록시를 지나며, 프록시가 쿠키를
`Authorization: Bearer`로 번역한다(SSE도 쿠키로 인증된다 — `EventSource`는 헤더를 못
보내지만 쿠키는 자동 전송). FastAPI는 JWKS로 access 토큰을 검증하고 `cognito:groups`를
역할로 매핑한다.

**Tech Stack:** AWS CDK 2.261 (TypeScript) · Amazon Cognito (Hosted UI v2 / managed login)
· FastAPI + PyJWT[crypto] · Next.js 15 App Router (middleware + route handlers) ·
pytest · Vitest + MSW · botocore Stubber

**스펙:** `docs/superpowers/specs/2026-07-25-cognito-auth-user-management-design.md`

## Global Constraints

- **역할은 `admin`과 `pm` 둘뿐.** 역할의 유일한 출처는 Cognito 그룹 멤버십
  (`cognito:groups` 클레임). 커스텀 속성으로 role을 두지 않는다.
- **`admin` = `pm`의 모든 권한 + `/admin/*`.** 두 역할 모두 모든 프로젝트에
  접근·생성·삭제할 수 있다. 프로젝트별 소유권은 만들지 않는다.
- **시드 계정 비밀번호는 정확히 `PathFinder2026!@`** (14자, 대/소/숫자/기호 포함).
- **시드 계정:** `admin@pathfinder.local`(그룹 `admin`), `pm@pathfinder.local`(그룹 `pm`).
- **`PATHFINDER_COGNITO_USER_POOL_ID`가 빈 문자열이거나 미설정이면 인증 전체를
  바이패스**하고 `Principal(username="local-dev", sub="local-dev", role="admin")`을
  반환한다. 기존 pytest 53개 파일은 **무수정으로 통과해야 한다**.
- **access 토큰 검증은 `client_id` 클레임으로 한다 — `aud`가 아니다.** PyJWT는
  `options={"verify_aud": False}`로 호출하고 `client_id`를 코드에서 비교한다.
- **access 토큰에 `email`이 없다.** `Principal`은 email을 담지 않는다. 화면 표시용
  이메일은 프론트가 id 토큰(`pf_id` 쿠키)에서 읽는다.
- **Cognito `Username` == 이메일.** 풀은 `signInAliases: { username: true, email: true }`
  (→ CFN `AliasAttributes: ['email']`)로 만들어 호출자가 username을 지정한다.
  `email_verified: true`가 필수다(alias 사인인 조건).
- **공개(무인증) 경로는 정확히 둘:** `/survey/{token}`(`surveys_public.py`),
  `/proto/{pid}/{slug}` 및 `/proto/{pid}/{slug}/{path:path}`(`proto_public.py`).
  그 외 모든 백엔드 라우트는 인증이 필요하다.
- **쿠키 이름:** `pf_access`, `pf_id`, `pf_refresh`. 속성: `httpOnly`, `sameSite: "lax"`,
  `path: "/"`, `secure`는 프로덕션에서만.
- **Cognito 관련 프론트 env에 `NEXT_PUBLIC_` 접두어를 절대 붙이지 않는다** — 붙이면
  클라이언트 번들에 인라인되어 시크릿이 브라우저로 나간다.
- **커밋은 각 Task 끝에서.** 커밋 메시지는 한국어 본문 + Conventional Commits 접두어.
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`로 끝낸다.
- **테스트 실행 경로:** 백엔드 `cd backend && .venv/bin/python -m pytest`,
  프론트 `cd frontend && npx vitest run`, 인프라 `cd infra && npx ts-node test/<f>.assert.ts`.

---

## File Structure

**신규 (인프라)**

| 파일 | 책임 |
|---|---|
| `infra/lib/auth-client-config.ts` | 앱 클라이언트 설정의 **단일 출처**. AuthStack과 콜백 주입 커스텀 리소스가 같은 값을 쓰게 한다 |
| `infra/lib/seed-users.ts` | 시드 계정 1명을 만드는 `seedUser()` 헬퍼 (커스텀 리소스 3개) |
| `infra/lib/pathfinder-auth-stack.ts` | User Pool · 그룹 2개 · Hosted UI v2 도메인 · 브랜딩 · 앱 클라이언트 · 시드 호출 2회 |
| `infra/test/auth-stack.assert.ts` | 합성 템플릿 단정 |

**신규 (백엔드)**

| 파일 | 책임 |
|---|---|
| `backend/pathfinder/auth/__init__.py` | 빈 패키지 마커 |
| `backend/pathfinder/auth/models.py` | `Principal` 데이터클래스 + `Role` 리터럴 |
| `backend/pathfinder/auth/verifier.py` | JWKS 캐시 + access 토큰 검증 → `Principal` |
| `backend/pathfinder/auth/deps.py` | `require_user` / `require_admin` FastAPI 의존성 + 바이패스 |
| `backend/pathfinder/auth/cognito.py` | Cognito Admin* API 래퍼 (boto3 호출을 라우트에서 분리 — Stubber 테스트 가능) |
| `backend/pathfinder/routes/admin_users.py` | `/admin/users*` 7개 라우트 |
| `backend/pathfinder/routes/proto_public.py` | `prototypes.py`에서 옮겨온 `/proto/*` 공개 프록시 |

**신규 (프론트)**

| 파일 | 책임 |
|---|---|
| `frontend/lib/auth/cognitoUrls.ts` | Hosted UI authorize/token/logout URL 조립 (순수 함수 — 테스트 가능) |
| `frontend/lib/auth/pkce.ts` | code_verifier/challenge 생성, state 생성 |
| `frontend/lib/auth/claims.ts` | JWT payload 디코드(**검증 없음**) + `roleFromClaims()` |
| `frontend/lib/auth/cookies.ts` | 쿠키 이름 상수 + 세팅/삭제 헬퍼 |
| `frontend/app/api/auth/login/route.ts` | PKCE 시작 → Hosted UI 리다이렉트 |
| `frontend/app/api/auth/callback/route.ts` | state 검증 → 토큰 교환 → 쿠키 → 복귀 |
| `frontend/app/api/auth/logout/route.ts` | 쿠키 삭제 → Cognito 로그아웃 |
| `frontend/app/api/auth/me/route.ts` | email·role 반환 |
| `frontend/middleware.ts` | 미인증 리다이렉트 + `/admin` UX 게이트 |
| `frontend/app/login/page.tsx` | 로그인 화면 |
| `frontend/app/admin/users/page.tsx` | 사용자 관리 화면 |
| `frontend/components/admin/UserTable.tsx` | 사용자 표 + 행 액션 |
| `frontend/components/admin/InviteUserModal.tsx` | 초대 모달 + 임시 비밀번호 1회 표시 |
| `frontend/components/admin/TempPasswordPanel.tsx` | 임시 비밀번호 표시·복사 (초대/재설정 공용) |
| `frontend/components/UserMenu.tsx` | 헤더 사용자 드롭다운 |
| `frontend/lib/api/adminUsers.ts` | 관리 API 클라이언트 |
| `frontend/lib/auth/sessionRecovery.ts` | SSE 오류 후 세션 만료 판정 → `/login` 이동 |

**수정**

| 파일 | 변경 |
|---|---|
| `backend/pyproject.toml` | `PyJWT[crypto]>=2.8` 추가 |
| `backend/pathfinder/app.py` | Cognito env 접근자 + 라우터 include에 인증 의존성 + `proto_public` 등록 |
| `backend/pathfinder/routes/prototypes.py` | `/proto/*` 프록시 코드를 `proto_public.py`로 이동 |
| `backend/tests/test_routes_prototypes.py` | `_rewritten_location` import 경로 변경 |
| `frontend/lib/auth.ts` | `getAuthToken()` 삭제 → `credentials: "include"` 상수 |
| `frontend/lib/api/{client,http,prototypes}.ts` | 헤더 대신 `credentials: "include"` |
| `frontend/components/AppHeader.tsx` | 하드코딩 "김PM" → `<UserMenu />` |
| `frontend/lib/useTurnStream.ts` · `usePrototypeStream.ts` · `useWorkspaceStream.ts` | SSE 오류 시 세션 확인 → 만료면 `/login` |
| `infra/bin/app.ts` | AuthStack 추가 + hosting에 참조 전달 |
| `infra/lib/pathfinder-hosting-stack.ts` | 콜백 주입 커스텀 리소스 + Cognito env/IAM |
| `infra/lib/user-data.ts` | 백엔드 env 3개 + 프론트 env 4개 + 시크릿 조회 |
| `README.md` · `infra/README.md` | 인증 절차 · env · 시드 계정 경고 |

**Task 순서와 의존성**

```
Task 1 (auth-client-config)  ─┐
Task 2 (seed-users)          ─┼─► Task 3 (AuthStack + assert)
                              ┘
Task 4 (Principal + verifier)  ─► Task 5 (deps + 바이패스)
                                    │
Task 6 (proto_public 분리) ─────────┼─► Task 7 (app.py 전역 적용)
                                    │
Task 8 (cognito.py 래퍼) ───────────┴─► Task 9 (admin_users 라우트)
                                          │
Task 10 (프론트 auth lib) ─► Task 11 (route handlers) ─► Task 12 (middleware)
                                          │
Task 13 (프록시 Bearer+refresh) ─► Task 14 (기존 클라이언트 전환)
                                          │
Task 15 (관리 API 클라이언트+화면) ─► Task 16 (UserMenu+로그인 화면)
                                          │
Task 18 (SSE 401 -> /login)
                                          │
Task 17 (hosting 배선 + user-data) ─► Task 19 (문서 + e2e 체크리스트)
```

Task 1–3(인프라), 4–9(백엔드), 10–16·18(프론트)는 서로 독립적이라 병렬 실행 가능하다.
Task 17은 3과 7이 끝난 뒤에만 의미가 있다.

---

## Task 1: 앱 클라이언트 설정 단일 출처

**Files:**
- Create: `infra/lib/auth-client-config.ts`
- Test: `infra/test/auth-client-config.assert.ts`

**Interfaces:**
- Consumes: 없음 (순수 함수)
- Produces:
  - `SEED_ADMIN_EMAIL = 'admin@pathfinder.local'`
  - `SEED_PM_EMAIL = 'pm@pathfinder.local'`
  - `SEED_PASSWORD = 'PathFinder2026!@'`
  - `GROUP_ADMIN = 'admin'`, `GROUP_PM = 'pm'`
  - `CALLBACK_PATH = '/api/auth/callback'`, `LOGOUT_PATH = '/login'`
  - `callbackUrls(appUrls: string[]): string[]`
  - `logoutUrls(appUrls: string[]): string[]`
  - `LOCAL_APP_URL = 'http://localhost:3000'`
  - `OAUTH_SCOPES = ['openid', 'email', 'profile']`

**왜 이 파일이 따로 있는가:** `UpdateUserPoolClient`는 PUT 시맨틱이라 지정하지 않은
필드를 지운다(스펙 §3.5). AuthStack의 클라이언트 정의와 HostingStack의 갱신 호출이
같은 값을 써야 하고, 두 스택이 서로를 import하면 순환이 생긴다. 순수 상수 모듈이
양쪽의 공통 출처가 된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`infra/test/auth-client-config.assert.ts`:

```typescript
import * as assert from 'node:assert';
import {
  CALLBACK_PATH, LOCAL_APP_URL, LOGOUT_PATH, OAUTH_SCOPES,
  SEED_ADMIN_EMAIL, SEED_PASSWORD, SEED_PM_EMAIL,
  callbackUrls, logoutUrls,
} from '../lib/auth-client-config';

// 시드 비밀번호는 스펙이 못박은 값이다. 오타가 나면 배포는 성공하고 로그인만
// 실패하므로(디버깅이 어렵다) 상수 자체를 단정한다.
assert.strictEqual(SEED_PASSWORD, 'PathFinder2026!@');
assert.strictEqual(SEED_ADMIN_EMAIL, 'admin@pathfinder.local');
assert.strictEqual(SEED_PM_EMAIL, 'pm@pathfinder.local');

// 콜백 URL은 와일드카드가 불가하고 전수 일치만 허용된다 — 경로가 프론트
// route handler 경로와 정확히 같아야 한다.
assert.strictEqual(CALLBACK_PATH, '/api/auth/callback');
assert.strictEqual(LOGOUT_PATH, '/login');

// 여러 앱 URL(로컬 + CloudFront)에 대해 각각의 콜백을 만든다.
assert.deepStrictEqual(
  callbackUrls([LOCAL_APP_URL, 'https://d123.cloudfront.net']),
  ['http://localhost:3000/api/auth/callback',
   'https://d123.cloudfront.net/api/auth/callback'],
);
assert.deepStrictEqual(
  logoutUrls(['https://d123.cloudfront.net']),
  ['https://d123.cloudfront.net/login'],
);

// 후행 슬래시가 붙은 앱 URL이 들어와도 이중 슬래시를 만들지 않는다.
assert.deepStrictEqual(
  callbackUrls(['https://d123.cloudfront.net/']),
  ['https://d123.cloudfront.net/api/auth/callback'],
);

// 중복 URL은 제거한다 — Cognito가 중복 콜백 URL을 거부한다.
assert.deepStrictEqual(
  callbackUrls([LOCAL_APP_URL, LOCAL_APP_URL]),
  ['http://localhost:3000/api/auth/callback'],
);

assert.deepStrictEqual(OAUTH_SCOPES, ['openid', 'email', 'profile']);
console.log('OK  auth-client-config: seed constants + callback/logout URL derivation');
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd infra && npx ts-node test/auth-client-config.assert.ts`
Expected: FAIL — `Cannot find module '../lib/auth-client-config'`

- [ ] **Step 3: 최소 구현을 쓴다**

`infra/lib/auth-client-config.ts`:

```typescript
// 앱 클라이언트 설정의 단일 출처.
//
// 왜 별도 모듈인가: 콜백 URL은 배포 시점에 정해지는 CloudFront 도메인을 포함해야
// 하는데, AuthStack이 만든 클라이언트를 HostingStack이 UpdateUserPoolClient로
// 갱신한다. 그 API는 PUT 시맨틱이어서 지정하지 않은 필드를 지우므로, 두 곳이
// 같은 값을 봐야 한다. 스택 간 import는 순환을 만들기 때문에 순수 상수 모듈로 뺀다.

export const SEED_ADMIN_EMAIL = 'admin@pathfinder.local';
export const SEED_PM_EMAIL = 'pm@pathfinder.local';

// 데모/워크숍용 사전 설정 비밀번호. ⚠️ CloudFormation 템플릿과 스택 이벤트에
// 평문으로 남는다 — 운영 전환 시 반드시 교체한다(스펙 §4.1).
export const SEED_PASSWORD = 'PathFinder2026!@';

export const GROUP_ADMIN = 'admin';
export const GROUP_PM = 'pm';

// 프론트 route handler / 로그인 화면의 실제 경로와 반드시 일치해야 한다.
export const CALLBACK_PATH = '/api/auth/callback';
export const LOGOUT_PATH = '/login';

export const LOCAL_APP_URL = 'http://localhost:3000';
export const OAUTH_SCOPES = ['openid', 'email', 'profile'];

function join(appUrl: string, path: string): string {
  return `${appUrl.replace(/\/$/, '')}${path}`;
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

export function callbackUrls(appUrls: string[]): string[] {
  return unique(appUrls.map((u) => join(u, CALLBACK_PATH)));
}

export function logoutUrls(appUrls: string[]): string[] {
  return unique(appUrls.map((u) => join(u, LOGOUT_PATH)));
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd infra && npx ts-node test/auth-client-config.assert.ts`
Expected: `OK  auth-client-config: seed constants + callback/logout URL derivation`

- [ ] **Step 5: `package.json`의 test 스크립트에 추가한다**

`infra/package.json`의 `scripts.test`를 다음으로 바꾼다:

```json
"test": "ts-node test/user-data.assert.ts && ts-node test/hosting-stack.assert.ts && ts-node test/auth-client-config.assert.ts && ts-node test/auth-stack.assert.ts"
```

> `auth-stack.assert.ts`는 Task 3에서 만든다. 이 시점에 `npm test`를 돌리면 그
> 파일이 없어 실패하는 것이 정상이다 — Task 3 끝에서 초록이 된다.

- [ ] **Step 6: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/auth-client-config.ts infra/test/auth-client-config.assert.ts infra/package.json
git commit -m "$(cat <<'EOF'
feat(infra): 앱 클라이언트 설정 단일 출처 모듈

UpdateUserPoolClient가 PUT 시맨틱이라 AuthStack의 클라이언트 정의와
HostingStack의 콜백 갱신이 같은 값을 봐야 한다. 스택 간 import는 순환이므로
순수 상수 모듈로 뺀다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 시드 계정 헬퍼

**Files:**
- Create: `infra/lib/seed-users.ts`

**Interfaces:**
- Consumes: `SEED_PASSWORD` from `./auth-client-config` (Task 1)
- Produces:
  - `export interface SeedUserProps { userPool: cognito.IUserPool; email: string; group: string; password: string; }`
  - `export function seedUser(scope: Construct, id: string, props: SeedUserProps): void`

**설계 노트 (구현자가 반드시 알아야 할 것):**

3개의 `AwsCustomResource`를 만들고 `node.addDependency()`로 순서를 강제한다.

1. `AdminCreateUser` — `Username: email`, `MessageAction: 'SUPPRESS'`,
   `UserAttributes: [{Name:'email',Value:email},{Name:'email_verified',Value:'true'}]`
2. `AdminSetUserPassword` — `Username: email`, `Password: password`, `Permanent: true`
3. `AdminAddUserToGroup` — `Username: email`, `GroupName: group`

**핵심: 2·3단계는 1단계 응답을 참조하지 않는다.** 같은 `email` 상수를 `Username`으로
직접 넘긴다. 재배포 시 1단계가 `UsernameExistsException`으로 무시되면 응답 필드가
비는데, 거기에 의존하면 2·3단계가 깨진다.

`AdminCreateUser`만 `ignoreErrorCodesMatching: 'UsernameExistsException'`을 붙인다.
2·3단계는 멱등이므로(같은 비밀번호 재설정, 이미 속한 그룹 재추가) 무시가 필요 없다.

각 리소스는 `onCreate`와 `onUpdate` **양쪽**에 같은 호출을 걸고
`physicalResourceId: cr.PhysicalResourceId.of(\`${email}-<stage>\`)`로 고정한다.
`onUpdate`가 필요한 이유: 재배포마다 비밀번호를 알려진 값으로 되돌린다(스펙 §4).

- [ ] **Step 1: 구현을 쓴다**

`infra/lib/seed-users.ts`:

```typescript
import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';

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
        Username: email,
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
  // Username에 1단계의 응답이 아니라 같은 email 상수를 쓴다: 1단계가
  // UsernameExistsException으로 무시되면 응답 필드가 비어 getResponseField가
  // 깨진다. 풀이 AliasAttributes(호출자 지정 username)라서 이렇게 할 수 있다.
  const setPassword = new cr.AwsCustomResource(scope, `${id}Password`, {
    onCreate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminSetUserPassword',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: email,
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
        Username: email,
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
        Username: email,
        GroupName: group,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-group-${group}`),
    },
    onUpdate: {
      service: 'CognitoIdentityServiceProvider',
      action: 'adminAddUserToGroup',
      parameters: {
        UserPoolId: userPool.userPoolId,
        Username: email,
        GroupName: group,
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${email}-group-${group}`),
    },
    policy,
    installLatestAwsSdk: false,
  });
  addToGroup.node.addDependency(setPassword);
}
```

- [ ] **Step 2: 타입 체크가 통과하는지 확인한다**

Run: `cd infra && npx tsc --noEmit`
Expected: 에러 없음 (출력 없음)

> 이 Task에 독립 테스트가 없는 이유: `seedUser()`의 관찰 가능한 산출물은 합성된
> CloudFormation 템플릿이고, 그건 스택 컨텍스트가 있어야 만들어진다. Task 3의
> `auth-stack.assert.ts`가 이 헬퍼가 만든 리소스 6개를 단정한다.

- [ ] **Step 3: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/seed-users.ts
git commit -m "$(cat <<'EOF'
feat(infra): 시드 계정 헬퍼 — 생성·비밀번호확정·그룹배정

CfnUserPoolUser로는 비밀번호를 Permanent로 확정할 수 없어 첫 로그인마다 변경을
요구한다. AdminSetUserPassword가 필요하고 그건 커스텀 리소스뿐이다.

2·3단계는 1단계 응답을 참조하지 않고 같은 이메일 상수를 Username으로 쓴다 —
재배포 시 1단계가 UsernameExistsException으로 무시되면 응답 필드가 비기 때문.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: AuthStack — User Pool · 그룹 · Hosted UI v2 · 클라이언트 · 시딩

**Files:**
- Create: `infra/lib/pathfinder-auth-stack.ts`
- Create: `infra/test/auth-stack.assert.ts`
- Modify: `infra/bin/app.ts`

**Interfaces:**
- Consumes: Task 1의 상수 전부, Task 2의 `seedUser()`
- Produces:
  - `export class PathfinderAuthStack extends cdk.Stack`
  - `readonly userPool: cognito.UserPool`
  - `readonly userPoolClient: cognito.UserPoolClient`
  - `readonly hostedUiDomain: string` — 예: `pathfinder-123456789012-ap-northeast-2.auth.ap-northeast-2.amazoncognito.com`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`infra/test/auth-stack.assert.ts`:

```typescript
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd infra && npx ts-node test/auth-stack.assert.ts`
Expected: FAIL — `Cannot find module '../lib/pathfinder-auth-stack'`

- [ ] **Step 3: AuthStack을 구현한다**

`infra/lib/pathfinder-auth-stack.ts`:

```typescript
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import {
  GROUP_ADMIN, GROUP_PM, LOCAL_APP_URL, OAUTH_SCOPES,
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

export class PathfinderAuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly hostedUiDomain: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // --- User Pool ---
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'pathfinder',
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
      description: 'Pathfinder 관리자 — PM 권한 + 사용자 관리',
      precedence: 0,
    });
    new cognito.CfnUserPoolGroup(this, 'PmGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: GROUP_PM,
      description: 'Pathfinder PM — 프로젝트 전체 접근, 사용자 관리 제외',
      precedence: 10,
    });

    // --- Hosted UI v2 (managed login) ---
    // 도메인 프리픽스는 계정·리전 안에서 유일해야 한다.
    const domainPrefix = `pathfinder-${account}-${region}`;
    const domain = this.userPool.addDomain('HostedUi', {
      cognitoDomain: { domainPrefix },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });
    this.hostedUiDomain = `${domainPrefix}.auth.${region}.amazoncognito.com`;

    // v2는 브랜딩 스타일 레코드가 있어야 정상 렌더된다(콘솔이 자동으로 하는 일).
    // 없으면 로그인 페이지가 깨진 채로 뜬다.
    const branding = new cognito.CfnManagedLoginBranding(this, 'Branding', {
      userPoolId: this.userPool.userPoolId,
      useCognitoProvidedValues: true,
    });
    branding.node.addDependency(domain);

    // --- 앱 클라이언트 ---
    // confidential(시크릿 있음): 코드 교환이 서버사이드(Next route handler)라
    // 시크릿을 안전히 보관할 수 있고, 두면 client_id만 훔친 코드 가로채기가 막힌다.
    // 콜백은 localhost만 — 실제 CloudFront 도메인은 HostingStack이 덧붙인다(§3.5).
    this.userPoolClient = this.userPool.addClient('WebClient', {
      userPoolClientName: 'pathfinder-web',
      generateSecret: true,
      authFlows: { userSrp: false, userPassword: false },
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: false },
        scopes: OAUTH_SCOPES.map((s) => SCOPE_MAP[s]),
        callbackUrls: callbackUrls([LOCAL_APP_URL]),
        logoutUrls: logoutUrls([LOCAL_APP_URL]),
      },
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
    });

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
```



- [ ] **Step 4: 통과를 확인한다**

Run: `cd infra && npx ts-node test/auth-stack.assert.ts`
Expected: `OK  auth stack: no-self-signup + alias username + groups + managed login v2 + code-only client + 6 seed resources`

단정이 실패하면 실제 합성 값을 보고 고친다:
```bash
cd infra && npx ts-node -e "
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { PathfinderAuthStack } from './lib/pathfinder-auth-stack';
const app = new cdk.App();
const s = new PathfinderAuthStack(app, 'Auth', { env: { account: '123456789012', region: 'ap-northeast-2' } });
console.log(JSON.stringify(Template.fromStack(s).toJSON().Resources, null, 2));
"
```

- [ ] **Step 5: `bin/app.ts`에 AuthStack을 등록한다**

`infra/bin/app.ts`를 다음으로 바꾼다:

```typescript
#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderAuthStack } from '../lib/pathfinder-auth-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

const app = new cdk.App();

// 리전 우선순위: CDK_DEPLOY_REGION > CDK_DEFAULT_REGION(프로파일) > 서울.
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { region, account };

const drill = new PathfinderDrillStack(app, 'PathfinderDrillStack', { env });

// 인증 스택: User Pool · 그룹 · Hosted UI v2 · 앱 클라이언트 · 시드 계정 2개.
// 콜백 URL은 localhost만 갖고 배포되며, 실제 CloudFront 도메인은 아래 호스팅
// 스택이 UpdateUserPoolClient로 덧붙인다(순환 의존 해소).
const auth = new PathfinderAuthStack(app, 'PathfinderAuthStack', { env });

// 호스팅 스택은 CloudFront origin-facing 프리픽스 리스트를 배포 리전에서
// 자동 조회한다(fromLookup) — synth/deploy 시 크리덴셜 필요, 결과는
// cdk.context.json에 캐시된다(커밋 대상).
new PathfinderHostingStack(app, 'PathfinderHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
  userPool: auth.userPool,
  userPoolClient: auth.userPoolClient,
  hostedUiDomain: auth.hostedUiDomain,
});
```

> 이 시점에 `PathfinderHostingStack`은 아직 새 props 3개를 받지 않으므로
> `npx tsc --noEmit`이 실패한다. **Task 17이 그 스택을 고친다.** 지금은
> `bin/app.ts` 변경을 커밋하지 말고 파일을 원상태로 되돌린 뒤, Task 17에서
> 함께 적용한다:
> ```bash
> git checkout infra/bin/app.ts
> ```
> AuthStack 자체는 `bin/app.ts` 없이도 테스트가 검증한다.

- [ ] **Step 6: 타입 체크 + 전체 인프라 테스트**

Run:
```bash
cd infra && npx tsc --noEmit && npm test
```
Expected: 네 개의 assert 파일이 모두 `OK …`를 출력

- [ ] **Step 7: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/pathfinder-auth-stack.ts infra/test/auth-stack.assert.ts
git commit -m "$(cat <<'EOF'
feat(infra): AuthStack — User Pool · 역할 그룹 · Hosted UI v2 · 시드 계정

selfSignUpEnabled:false가 AllowAdminCreateUserOnly로 떨어져 신규 가입을 막는다.
signInAliases에 username을 함께 켜 AliasAttributes로 합성시킨다 — email만 켜면
Cognito가 username을 UUID로 만들어 시딩이 비결정적이 된다.

Hosted UI v2는 ManagedLoginBranding(useCognitoProvidedValues) 없이는 렌더가
깨지므로 함께 만든다. 콜백은 localhost만 — CloudFront 도메인은 호스팅 스택이
덧붙인다(순환 의존 해소).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `Principal` + access 토큰 검증기

**Files:**
- Create: `backend/pathfinder/auth/__init__.py`
- Create: `backend/pathfinder/auth/models.py`
- Create: `backend/pathfinder/auth/verifier.py`
- Create: `backend/tests/test_auth_verifier.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `Role = Literal["admin", "pm"]`
  - `@dataclass(frozen=True) class Principal: username: str; sub: str; role: Role`
  - `class TokenError(Exception)` — 검증 실패
  - `class JwksCache` — `__init__(self, region: str, user_pool_id: str, http_get: Callable[[str], dict] | None = None)`,
    `async def key_for(self, kid: str) -> dict`, `def clear(self) -> None`
  - `async def verify_access_token(token: str, *, region: str, user_pool_id: str, client_id: str, jwks: JwksCache) -> Principal`

**구현자가 반드시 알아야 할 두 가지 (AWS 문서로 확인됨):**

1. **access 토큰은 `aud`가 아니라 `client_id`로 앱 클라이언트를 식별한다.**
   PyJWT에 `audience=`를 넘기면 `aud` 클레임이 없어 `MissingRequiredClaimError`가 난다.
   반드시 `options={"verify_aud": False}`로 디코드하고 `client_id`를 직접 비교한다.
2. **access 토큰에 `email`이 없다.** 기본 payload는 `sub`, `username`,
   `cognito:groups`, `iss`, `client_id`, `token_use`, `scope`, `exp`, `iat`다.
   그래서 `Principal`에 email 필드가 없다.

- [ ] **Step 1: `PyJWT[crypto]`를 의존성에 추가한다**

`backend/pyproject.toml`의 `dependencies` 리스트 끝에 `"PyJWT[crypto]>=2.8"`을 넣는다.
현재 줄:

```toml
dependencies = ["fastapi>=0.110", "pydantic>=2.6", "sse-starlette>=2.0", "httpx>=0.27", "boto3>=1.43.35", "uvicorn>=0.30", "python-dotenv>=1.0", "openpyxl>=3.1", "pypdf>=4.0", "python-multipart>=0.0.9", "strands-agents>=1.48,<2", "claude-agent-sdk==0.2.126"]
```

바꾼 뒤:

```toml
# PyJWT[crypto]: Cognito access 토큰의 RS256 서명 검증. 암호 코드를 직접 쓰지
# 않는다 — JWKS→공개키 변환과 서명 검증은 라이브러리에 맡긴다.
dependencies = ["fastapi>=0.110", "pydantic>=2.6", "sse-starlette>=2.0", "httpx>=0.27", "boto3>=1.43.35", "uvicorn>=0.30", "python-dotenv>=1.0", "openpyxl>=3.1", "pypdf>=4.0", "python-multipart>=0.0.9", "strands-agents>=1.48,<2", "claude-agent-sdk==0.2.126", "PyJWT[crypto]>=2.8"]
```

Run: `cd backend && .venv/bin/pip install -e ".[dev]"`
Expected: 성공 (PyJWT/cryptography는 이미 전이 의존성으로 설치돼 있어 빠르게 끝난다)

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`backend/tests/test_auth_verifier.py`:

```python
# backend/tests/test_auth_verifier.py
#
# 실 Cognito 없이 검증기를 시험한다: 테스트용 RSA 키로 토큰을 서명하고, 그 키의
# 공개 부분을 JWKS 형태로 주입한다. 문서 확인 사항 두 개가 이 테스트의 핵심이다 —
# access 토큰은 client_id로(aud 아님) 클라이언트를 식별하고, email 클레임이 없다.
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from pathfinder.auth.verifier import JwksCache, TokenError, verify_access_token

REGION = "ap-northeast-2"
POOL = "ap-northeast-2_TEST123"
CLIENT_ID = "client-abc"
ISS = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
KID = "test-key-1"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict:
    """테스트 키의 공개 부분을 Cognito JWKS 형태로 내놓는다."""
    from jwt.algorithms import RSAAlgorithm
    jwk = RSAAlgorithm.to_jwk(_private_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def _token(**overrides) -> str:
    """Cognito access 토큰의 기본 payload 형태(문서 확인).

    email이 없는 것이 의도다 — access 토큰에는 email 클레임이 존재하지 않는다.
    """
    now = int(time.time())
    claims = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "cognito:groups": ["admin"],
        "iss": ISS,
        "client_id": CLIENT_ID,
        "token_use": "access",
        "scope": "openid email profile",
        "auth_time": now,
        "iat": now,
        "exp": now + 3600,
        "username": "admin@pathfinder.local",
    }
    claims.update(overrides)
    return jwt.encode(claims, _private_key, algorithm="RS256",
                      headers={"kid": KID})


def _cache(jwks: dict | None = None, calls: list | None = None) -> JwksCache:
    payload = jwks if jwks is not None else _jwks()

    def http_get(url: str) -> dict:
        if calls is not None:
            calls.append(url)
        return payload

    return JwksCache(region=REGION, user_pool_id=POOL, http_get=http_get)


async def _verify(token: str, *, cache: JwksCache | None = None,
                  client_id: str = CLIENT_ID):
    return await verify_access_token(
        token, region=REGION, user_pool_id=POOL, client_id=client_id,
        jwks=cache or _cache())


async def test_valid_token_yields_principal_with_role_from_groups():
    principal = await _verify(_token())
    assert principal.username == "admin@pathfinder.local"
    assert principal.sub == "11111111-2222-3333-4444-555555555555"
    assert principal.role == "admin"


async def test_pm_group_yields_pm_role():
    principal = await _verify(_token(**{"cognito:groups": ["pm"]}))
    assert principal.role == "pm"


async def test_admin_wins_when_user_is_in_both_groups():
    # 두 그룹에 모두 속하면 더 넓은 권한(admin)으로 해석한다 — 그래야 관리자를
    # pm 그룹에 추가하는 실수가 권한을 조용히 깎지 않는다.
    principal = await _verify(_token(**{"cognito:groups": ["pm", "admin"]}))
    assert principal.role == "admin"


async def test_expired_token_is_rejected():
    now = int(time.time())
    with pytest.raises(TokenError):
        await _verify(_token(exp=now - 10, iat=now - 3600))


async def test_wrong_client_id_is_rejected():
    # access 토큰은 aud가 아니라 client_id로 앱 클라이언트를 식별한다.
    with pytest.raises(TokenError):
        await _verify(_token(client_id="someone-elses-client"))


async def test_wrong_issuer_is_rejected():
    with pytest.raises(TokenError):
        await _verify(_token(iss="https://cognito-idp.us-east-1.amazonaws.com/other"))


async def test_id_token_is_rejected():
    # id 토큰을 access 토큰 자리에 넣는 혼동을 막는다.
    with pytest.raises(TokenError):
        await _verify(_token(token_use="id"))


async def test_token_without_known_group_is_rejected():
    # 그룹이 역할의 유일한 출처다. 어느 그룹에도 없으면 역할이 없으므로 거부한다.
    with pytest.raises(TokenError):
        await _verify(_token(**{"cognito:groups": []}))
    with pytest.raises(TokenError):
        await _verify(_token(**{"cognito:groups": ["some-other-group"]}))


async def test_missing_groups_claim_is_rejected():
    # 클레임 자체가 없는 경우(빈 배열과 구분). _token()은 항상 groups를 넣으므로
    # 여기서만 직접 서명한다.
    now = int(time.time())
    token = jwt.encode(
        {"sub": "s-1", "iss": ISS, "client_id": CLIENT_ID, "token_use": "access",
         "iat": now, "exp": now + 3600, "username": "u@x.io"},
        _private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(TokenError):
        await _verify(token)


async def test_garbage_token_is_rejected():
    with pytest.raises(TokenError):
        await _verify("not-a-jwt")


async def test_jwks_is_fetched_once_and_cached():
    calls: list[str] = []
    cache = _cache(calls=calls)
    await _verify(_token(), cache=cache)
    await _verify(_token(), cache=cache)
    assert len(calls) == 1, f"JWKS should be fetched once, got {calls}"
    assert calls[0] == (
        f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}/.well-known/jwks.json")


async def test_unknown_kid_refetches_jwks_then_fails():
    # 키 로테이션: 캐시에 없는 kid를 보면 한 번 재조회한다. 재조회해도 없으면
    # 실패하지만, 매 요청 재조회로 번지지는 않아야 한다.
    calls: list[str] = []
    cache = _cache(calls=calls)
    await _verify(_token(), cache=cache)          # 캐시 채움 (fetch 1)
    other = jwt.encode({"sub": "x"}, _private_key, algorithm="RS256",
                       headers={"kid": "rotated-key"})
    with pytest.raises(TokenError):
        await _verify(other, cache=cache)          # kid 미스 → fetch 2
    assert len(calls) == 2, f"unknown kid must trigger exactly one refetch, got {calls}"


async def test_jwks_fetch_failure_is_a_token_error():
    # fail-closed: JWKS를 못 받으면 통과시키지 않는다.
    def boom(url: str) -> dict:
        raise RuntimeError("network down")

    cache = JwksCache(region=REGION, user_pool_id=POOL, http_get=boom)
    with pytest.raises(TokenError):
        await _verify(_token(), cache=cache)
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_verifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.auth'`

- [ ] **Step 4: `models.py`를 구현한다**

`backend/pathfinder/auth/__init__.py`: 빈 파일로 만든다.

```bash
cd /home/ec2-user/project/pathfinder-sp/backend
mkdir -p pathfinder/auth
: > pathfinder/auth/__init__.py
```

`backend/pathfinder/auth/models.py`:

```python
# backend/pathfinder/auth/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 역할은 둘뿐이고, 출처는 Cognito 그룹 멤버십(cognito:groups)이다. 커스텀 속성으로
# role을 두지 않는다 — 진실이 두 곳에 생기면 어긋난다.
Role = Literal["admin", "pm"]

ROLE_ADMIN: Role = "admin"
ROLE_PM: Role = "pm"


@dataclass(frozen=True)
class Principal:
    """검증을 통과한 요청자.

    email이 없는 것이 의도다: Cognito **access** 토큰에는 email 클레임이 존재하지
    않는다(기본 payload는 sub/username/cognito:groups/client_id/token_use/scope).
    화면에 표시할 이메일은 프론트가 id 토큰에서 읽는다.
    """
    username: str
    sub: str
    role: Role
```

- [ ] **Step 5: `verifier.py`를 구현한다**

`backend/pathfinder/auth/verifier.py`:

```python
# backend/pathfinder/auth/verifier.py
#
# Cognito access 토큰 검증. 서명 검증은 PyJWT에 맡긴다(암호 코드를 직접 쓰지 않는다).
#
# 문서로 확인한 두 가지가 이 파일의 형태를 결정한다:
#
#   1) access 토큰은 `aud`가 아니라 `client_id` 클레임으로 앱 클라이언트를 식별한다.
#      PyJWT에 audience=를 넘기면 aud가 없어 MissingRequiredClaimError가 난다 —
#      verify_aud를 끄고 client_id를 직접 비교한다.
#      https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html
#   2) access 토큰에는 email이 없다. Principal이 email을 담지 않는 이유다.
#      https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-access-token.html
from __future__ import annotations

import asyncio
import logging
from typing import Callable

import jwt
from jwt.algorithms import RSAAlgorithm

from pathfinder.auth.models import ROLE_ADMIN, ROLE_PM, Principal, Role

_log = logging.getLogger(__name__)

_GROUPS_CLAIM = "cognito:groups"


class TokenError(Exception):
    """토큰이 신뢰할 수 없다. 라우트 계층이 401로 번역한다."""


def _default_http_get(url: str) -> dict:
    # httpx는 이미 의존성이다(백엔드가 프로토타입 프록시에 쓴다).
    import httpx
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


class JwksCache:
    """user pool의 JWKS를 kid→키로 캐시한다.

    조회는 kid 미스에서만 재시도한다(키 로테이션 대응). 매 요청 재조회는 Cognito를
    때리고 지연을 만들며, 반대로 영구 캐시는 로테이션 후 모든 토큰을 거부한다.
    """

    def __init__(self, region: str, user_pool_id: str,
                 http_get: Callable[[str], dict] | None = None) -> None:
        self._url = (f"https://cognito-idp.{region}.amazonaws.com/"
                     f"{user_pool_id}/.well-known/jwks.json")
        self._http_get = http_get or _default_http_get
        self._keys: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._keys = {}

    async def _fetch(self) -> None:
        # 동기 http_get을 스레드로 밀어 이벤트 루프를 막지 않는다.
        try:
            payload = await asyncio.to_thread(self._http_get, self._url)
        except Exception as exc:  # 네트워크·HTTP·JSON 무엇이든
            raise TokenError(f"jwks fetch failed: {exc}") from exc
        keys = {k["kid"]: k for k in payload.get("keys", []) if "kid" in k}
        if not keys:
            raise TokenError("jwks response contained no usable keys")
        self._keys = keys

    async def key_for(self, kid: str) -> dict:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            # double-check: 락을 기다리는 동안 다른 요청이 이미 채웠을 수 있다.
            if kid in self._keys:
                return self._keys[kid]
            await self._fetch()
        key = self._keys.get(kid)
        if key is None:
            raise TokenError(f"unknown signing key: {kid}")
        return key


def _role_from_groups(groups: object) -> Role:
    """그룹 멤버십을 역할로 바꾼다.

    두 그룹에 모두 속하면 admin으로 해석한다 — 관리자를 pm 그룹에 추가하는 실수가
    권한을 조용히 깎지 않게 한다. 어느 그룹에도 없으면 역할이 없으므로 거부한다.
    """
    if not isinstance(groups, list):
        raise TokenError("token has no cognito:groups claim")
    names = {str(g) for g in groups}
    if ROLE_ADMIN in names:
        return ROLE_ADMIN
    if ROLE_PM in names:
        return ROLE_PM
    raise TokenError(f"user belongs to no known role group: {sorted(names)}")


async def verify_access_token(token: str, *, region: str, user_pool_id: str,
                              client_id: str, jwks: JwksCache) -> Principal:
    """서명·발급자·만료·용도·클라이언트를 검증하고 Principal을 낸다.

    어떤 실패도 TokenError다 — 호출자가 실패 사유별로 분기하지 않도록.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError(f"malformed token header: {exc}") from exc
    kid = header.get("kid")
    if not kid:
        raise TokenError("token header has no kid")

    jwk = await jwks.key_for(kid)
    try:
        public_key = RSAAlgorithm.from_jwk(jwk)
    except Exception as exc:
        raise TokenError(f"unusable signing key: {exc}") from exc

    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=issuer,
            # access 토큰에는 aud가 없다 — client_id를 아래에서 직접 비교한다.
            options={"verify_aud": False, "require": ["exp", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(f"token rejected: {exc}") from exc

    if claims.get("token_use") != "access":
        raise TokenError(f"expected an access token, got {claims.get('token_use')!r}")
    if claims.get("client_id") != client_id:
        raise TokenError("token was issued to a different app client")

    username = claims.get("username")
    sub = claims.get("sub")
    if not username or not sub:
        raise TokenError("token is missing username/sub")

    return Principal(username=str(username), sub=str(sub),
                     role=_role_from_groups(claims.get(_GROUPS_CLAIM)))
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_verifier.py -q`
Expected: 13 passed

- [ ] **Step 7: 기존 스위트가 깨지지 않았는지 확인한다**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 기존 테스트 전부 통과 + 신규 13개 통과

- [ ] **Step 8: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/auth/__init__.py backend/pathfinder/auth/models.py \
        backend/pathfinder/auth/verifier.py backend/tests/test_auth_verifier.py \
        backend/pyproject.toml
git commit -m "$(cat <<'EOF'
feat(auth): Cognito access 토큰 검증기 + Principal

문서 확인 두 가지가 구현을 결정한다:
- access 토큰은 aud가 아니라 client_id로 앱 클라이언트를 식별한다. PyJWT에
  audience=를 넘기면 aud가 없어 실패하므로 verify_aud를 끄고 직접 비교한다.
- access 토큰에 email이 없다 → Principal은 username/sub/role만 담는다.

JWKS는 kid 미스에서만 재조회한다(키 로테이션 대응). 조회 실패는 fail-closed.
그룹이 역할의 유일한 출처이므로 어느 그룹에도 없으면 거부한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `require_user` / `require_admin` 의존성 + 로컬 바이패스

**Files:**
- Create: `backend/pathfinder/auth/deps.py`
- Create: `backend/tests/test_auth_deps.py`
- Modify: `backend/pathfinder/app.py` (env 접근자 + JWKS 싱글턴만 — 라우터 적용은 Task 7)

**Interfaces:**
- Consumes: Task 4의 `Principal`, `TokenError`, `JwksCache`, `verify_access_token`
- Produces:
  - `async def require_user(request: Request) -> Principal` — FastAPI 의존성
  - `async def require_admin(principal: Principal = Depends(require_user)) -> Principal`
  - `LOCAL_PRINCIPAL = Principal(username="local-dev", sub="local-dev", role="admin")`
- `app.py`가 새로 노출하는 것 (monkeypatch 지점):
  - `def cognito_config() -> dict | None` — 미설정이면 `None`(바이패스 신호)
  - `def jwks_cache() -> JwksCache` — 싱글턴

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_auth_deps.py`:

```python
# backend/tests/test_auth_deps.py
#
# 의존성 두 개의 계약: 인증이 설정되지 않았으면(로컬/테스트) 전부 통과시키고,
# 설정됐으면 Bearer 토큰을 검증하며 admin 전용 자리에서 pm을 403으로 막는다.
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.auth.deps import require_admin, require_user
from pathfinder.auth.models import Principal
from pathfinder.auth.verifier import TokenError

REGION = "ap-northeast-2"
POOL = "ap-northeast-2_TEST123"
CLIENT_ID = "client-abc"


def _probe_app() -> FastAPI:
    """의존성만 노출하는 최소 앱 — 실 라우트와 얽히지 않게 한다."""
    app = FastAPI()

    @app.get("/any")
    async def any_role(p: Principal = Depends(require_user)):
        return {"username": p.username, "role": p.role}

    @app.get("/admin-only")
    async def admin_only(p: Principal = Depends(require_admin)):
        return {"username": p.username, "role": p.role}

    return app


@pytest.fixture()
def no_auth(monkeypatch):
    """인증 미설정 = 로컬 모드."""
    monkeypatch.setattr(app_module, "cognito_config", lambda: None)


@pytest.fixture()
def with_auth(monkeypatch):
    """인증 설정 + 검증기를 가짜로 갈아끼운다.

    반환된 dict의 'principals'에 토큰→Principal 매핑을 넣으면 그대로 통과하고,
    없는 토큰은 TokenError가 된다.
    """
    state: dict = {"principals": {}}
    monkeypatch.setattr(app_module, "cognito_config", lambda: {
        "region": REGION, "user_pool_id": POOL, "client_id": CLIENT_ID})
    monkeypatch.setattr(app_module, "jwks_cache", lambda: object())

    async def fake_verify(token, *, region, user_pool_id, client_id, jwks):
        assert region == REGION and user_pool_id == POOL and client_id == CLIENT_ID
        try:
            return state["principals"][token]
        except KeyError:
            raise TokenError("no such token")

    import pathfinder.auth.deps as deps_module
    monkeypatch.setattr(deps_module, "verify_access_token", fake_verify)
    return state


def test_bypass_lets_every_request_through_as_admin(no_auth):
    client = TestClient(_probe_app())
    # 인증 미설정 상태에서는 헤더가 아예 없어도 통과한다 — 기존 pytest 53개
    # 파일과 로컬 실행 절차가 무수정으로 유지되는 근거가 이것이다.
    body = client.get("/any").json()
    assert body == {"username": "local-dev", "role": "admin"}
    # 바이패스 principal은 admin이므로 관리 라우트도 열린다.
    assert client.get("/admin-only").status_code == 200


def test_missing_authorization_header_is_401(with_auth):
    client = TestClient(_probe_app())
    r = client.get("/any")
    assert r.status_code == 401
    # WWW-Authenticate는 401의 표준 동반 헤더다.
    assert r.headers.get("www-authenticate") == "Bearer"


def test_non_bearer_scheme_is_401(with_auth):
    client = TestClient(_probe_app())
    assert client.get("/any", headers={"Authorization": "Basic abc"}).status_code == 401


def test_invalid_token_is_401(with_auth):
    client = TestClient(_probe_app())
    r = client.get("/any", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_valid_admin_token_passes_both_dependencies(with_auth):
    with_auth["principals"]["tok-admin"] = Principal(
        username="admin@pathfinder.local", sub="s-1", role="admin")
    client = TestClient(_probe_app())
    headers = {"Authorization": "Bearer tok-admin"}
    assert client.get("/any", headers=headers).json()["role"] == "admin"
    assert client.get("/admin-only", headers=headers).status_code == 200


def test_pm_passes_require_user_but_is_403_on_require_admin(with_auth):
    with_auth["principals"]["tok-pm"] = Principal(
        username="pm@pathfinder.local", sub="s-2", role="pm")
    client = TestClient(_probe_app())
    headers = {"Authorization": "Bearer tok-pm"}
    assert client.get("/any", headers=headers).json()["role"] == "pm"
    r = client.get("/admin-only", headers=headers)
    # 인증은 됐고 권한이 없는 것 — 403이 맞다(401은 "다시 로그인하라"는 뜻).
    assert r.status_code == 403


def test_bearer_scheme_is_case_insensitive(with_auth):
    with_auth["principals"]["tok-admin"] = Principal(
        username="admin@pathfinder.local", sub="s-1", role="admin")
    client = TestClient(_probe_app())
    assert client.get("/any", headers={"Authorization": "bearer tok-admin"}
                      ).status_code == 200


def test_empty_user_pool_id_counts_as_unset(monkeypatch):
    # env를 빈 문자열로 내보내는 배포 스크립트가 인증을 조용히 켜지 않게 한다.
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", "")
    assert app_module.cognito_config() is None


def test_config_requires_both_pool_and_client(monkeypatch):
    # 풀만 있고 클라이언트가 없으면 client_id 검증을 할 수 없다. 반쯤 설정된
    # 상태로 인증을 켜면 모든 요청이 500이 되므로, 설정으로 보지 않는다.
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", "")
    assert app_module.cognito_config() is None


def test_config_is_read_when_both_present(monkeypatch):
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PATHFINDER_S3_REGION", REGION)
    monkeypatch.delenv("PATHFINDER_COGNITO_REGION", raising=False)
    cfg = app_module.cognito_config()
    assert cfg == {"region": REGION, "user_pool_id": POOL, "client_id": CLIENT_ID}


def test_cognito_region_env_overrides_s3_region(monkeypatch):
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PATHFINDER_S3_REGION", "ap-northeast-2")
    monkeypatch.setenv("PATHFINDER_COGNITO_REGION", "us-east-1")
    assert app_module.cognito_config()["region"] == "us-east-1"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_deps.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.auth.deps'`

- [ ] **Step 3: `app.py`에 env 접근자와 JWKS 싱글턴을 추가한다**

`backend/pathfinder/app.py`에서 `def durable_projects_enabled() -> bool:` 블록
**바로 앞**에 다음을 삽입한다:

```python
# ---- 인증 (routes/*, auth/deps.py) ----

_jwks_singleton = None


def cognito_config() -> dict | None:
    """Cognito 설정. 미설정이면 None = 인증 바이패스.

    durable_projects_enabled()와 같은 규율이다: 필수 env가 없으면 그 기능 전체를
    생략하고 로컬/테스트가 아무 설정 없이 돌게 한다. 풀 id와 client id가 둘 다
    있어야 설정으로 본다 — 반쯤 설정된 상태로 인증을 켜면 client_id 검증을 할 수
    없어 모든 요청이 실패한다. EC2 systemd 유닛은 항상 둘 다 심으므로
    프로덕션에서 바이패스가 켜질 수 없다.
    """
    pool = os.environ.get("PATHFINDER_COGNITO_USER_POOL_ID", "").strip()
    client = os.environ.get("PATHFINDER_COGNITO_CLIENT_ID", "").strip()
    if not pool or not client:
        return None
    region = (os.environ.get("PATHFINDER_COGNITO_REGION", "").strip()
              or os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2"))
    return {"region": region, "user_pool_id": pool, "client_id": client}


def jwks_cache():
    """JWKS 캐시 싱글턴 (monkeypatchable in tests)."""
    global _jwks_singleton
    if _jwks_singleton is None:
        from pathfinder.auth.verifier import JwksCache
        cfg = cognito_config() or {}
        _jwks_singleton = JwksCache(region=cfg.get("region", "ap-northeast-2"),
                                    user_pool_id=cfg.get("user_pool_id", ""))
    return _jwks_singleton
```

- [ ] **Step 4: `deps.py`를 구현한다**

`backend/pathfinder/auth/deps.py`:

```python
# backend/pathfinder/auth/deps.py
#
# FastAPI 의존성 두 개. 라우터 include 시점에 붙여 라우트 본문을 건드리지 않는다.
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from pathfinder.auth.models import Principal
from pathfinder.auth.verifier import TokenError, verify_access_token

_log = logging.getLogger(__name__)

# 인증 미설정(로컬/테스트) 상태의 가상 요청자. admin인 이유: 로컬에서 관리
# 페이지까지 그대로 열려야 개발 흐름이 끊기지 않는다.
LOCAL_PRINCIPAL = Principal(username="local-dev", sub="local-dev", role="admin")

_UNAUTHENTICATED = HTTPException(
    status_code=401, detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"})


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _UNAUTHENTICATED
    return token.strip()


async def require_user(request: Request) -> Principal:
    """admin·pm 모두 통과. 인증이 설정되지 않았으면 전부 통과."""
    # app을 지연 import한다: app.py가 라우터를 include하고 라우터가 이 모듈을
    # import하므로, 모듈 최상단 import는 순환이 된다.
    import pathfinder.app as app_module

    cfg = app_module.cognito_config()
    if cfg is None:
        return LOCAL_PRINCIPAL

    token = _bearer_token(request)
    try:
        return await verify_access_token(
            token, region=cfg["region"], user_pool_id=cfg["user_pool_id"],
            client_id=cfg["client_id"], jwks=app_module.jwks_cache())
    except TokenError as exc:
        # 사유는 로그에만 — 클라이언트에게 어떤 검증이 실패했는지 알려주지 않는다.
        _log.info("token rejected: %s", exc)
        raise _UNAUTHENTICATED from exc


async def require_admin(
        principal: Principal = Depends(require_user)) -> Principal:
    """admin만 통과. pm은 403 — 인증은 됐고 권한이 없는 상태다(401 아님)."""
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return principal
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_deps.py -q`
Expected: 11 passed

- [ ] **Step 6: 전체 스위트를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 기존 전부 + 신규 전부 통과

- [ ] **Step 7: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/auth/deps.py backend/tests/test_auth_deps.py backend/pathfinder/app.py
git commit -m "$(cat <<'EOF'
feat(auth): require_user / require_admin 의존성 + 로컬 바이패스

cognito_config()가 None이면(풀 id 또는 client id 미설정) 인증 전체를 바이패스하고
가상 admin을 반환한다 — durable_projects_enabled()와 같은 규율이라 기존 테스트
53개 파일과 로컬 실행 절차가 무수정으로 유지된다.

pm이 admin 전용 자리에 오면 403이다(401이 아니다 — 인증은 됐고 권한이 없다).
토큰 거부 사유는 로그에만 남기고 클라이언트에게는 알려주지 않는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `/proto/*` 공개 프록시를 별도 모듈로 분리

**Files:**
- Create: `backend/pathfinder/routes/proto_public.py`
- Modify: `backend/pathfinder/routes/prototypes.py` (351–452행 삭제 + import 정리)
- Modify: `backend/tests/test_routes_prototypes.py` (`_rewritten_location` import 경로 3곳)

**Interfaces:**
- Consumes: 없음 (코드 이동)
- Produces:
  - `proto_public.router` — `/proto/{pid}/{slug}`, `/proto/{pid}/{slug}/{path:path}`
  - `proto_public._rewritten_location(value: str, pid: str, slug: str) -> str`

**왜 이 Task가 있는가:** Task 7이 라우터 include 시점에 인증 의존성을 붙인다.
`/proto/*`는 공개로 남아야 하는데 지금은 인증이 필요한 라우트 9개와 같은 파일에 있어
파일 단위로 분리할 수 없다. **파일 경계를 인증 경계와 일치시키면** 나중에 실수로
공개 라우트를 늘리기 어려워진다.

이 Task는 **순수 리팩터링**이다 — 동작이 하나도 바뀌지 않아야 하고, 기존 프록시
테스트가 import 경로만 바꿔 그대로 통과해야 한다.

- [ ] **Step 1: 이동 전 기준선을 잡는다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -q`
Expected: 전부 통과. **통과 개수를 적어둔다** — 이동 후 같은 수여야 한다.

- [ ] **Step 2: `proto_public.py`를 만든다**

`backend/pathfinder/routes/prototypes.py`의 351행(`# ---- streaming reverse proxy ----`)
부터 파일 끝(452행)까지를 잘라 아래 헤더와 함께 새 파일로 옮긴다.

`backend/pathfinder/routes/proto_public.py`:

```python
# backend/pathfinder/routes/proto_public.py — PUBLIC prototype preview proxy.
#
# 이 파일이 prototypes.py에서 분리된 이유는 인증이다: app.py는 라우터 include
# 시점에 인증 의존성을 붙이는데, 이 두 라우트는 공개로 남아야 한다. 검증 설문
# 링크(/survey/{token})를 받은 사용자는 계정이 없는 상태로 프로토타입을 써야 한다.
#
# 파일 경계 = 인증 경계. 여기에 라우트를 추가하면 그것은 인터넷에 공개된다.
#
# ⚠️ 알려진 한계(의도된 것): pid와 slug를 아는 사람이면 누구나 접근할 수 있다.
# 방어선은 slug의 추측 난이도뿐인 얕은 보안이므로 프로토타입에 민감 데이터를
# 넣지 않는 것이 전제다.
from __future__ import annotations

import logging
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import (PlainTextResponse, RedirectResponse,
                                 StreamingResponse)

_log = logging.getLogger(__name__)

router = APIRouter()
```

그 뒤에 원본 351–452행의 내용(`_STRIP_REQUEST_HEADERS`부터 `proxy_prototype`의
마지막 줄까지)을 **그대로** 붙인다. `# ---- streaming reverse proxy ----` 주석 줄은
새 파일의 모듈 docstring이 그 역할을 하므로 생략한다.

- [ ] **Step 3: `prototypes.py`에서 이동한 부분을 지우고 import를 정리한다**

`prototypes.py`에서 351행부터 끝까지 삭제한다. 그 뒤 파일 상단 import에서 이제
쓰이지 않는 것들을 지운다 — 다음 명령으로 확인한다:

```bash
cd backend
for name in quote urlsplit httpx BackgroundTask PlainTextResponse RedirectResponse StreamingResponse; do
  echo "== $name: $(grep -c "\b$name\b" pathfinder/routes/prototypes.py)"
done
```

카운트가 **1**이면 import 줄에만 남은 것이므로 그 import를 지운다. 2 이상이면
남겨둔다. (`Response`와 `EventSourceResponse`는 남은 라우트가 계속 쓴다.)

`from urllib.parse import quote, urlsplit`가 통째로 불필요해지면 줄을 지우고,
한쪽만 남으면 남는 쪽만 import한다.

- [ ] **Step 4: `app.py`에 새 라우터를 등록한다**

`backend/pathfinder/app.py` 끝의 라우터 include 블록에서 `prototypes` include
**바로 뒤**에 추가한다:

```python
from pathfinder.routes import proto_public  # noqa: E402
app.include_router(proto_public.router)
```

> Task 7이 이 include에 "인증 없음"을 명시하는 주석을 붙인다.

- [ ] **Step 5: 테스트의 import 경로를 고친다**

`backend/tests/test_routes_prototypes.py`에서 3곳을 바꾼다
(573, 580, 586행 근처 — `from pathfinder.routes.prototypes import _rewritten_location`):

```bash
cd backend
sed -i 's/from pathfinder.routes.prototypes import _rewritten_location/from pathfinder.routes.proto_public import _rewritten_location/' \
  tests/test_routes_prototypes.py
grep -n "_rewritten_location" tests/test_routes_prototypes.py
```

Expected: 6줄이 나오고, `import` 줄 3개가 모두 `proto_public`을 가리킨다.

- [ ] **Step 6: 동작이 하나도 바뀌지 않았음을 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -q`
Expected: Step 1과 **동일한** 통과 개수

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전체 통과

- [ ] **Step 7: 라우트 등록 상태를 눈으로 확인한다**

```bash
cd backend && .venv/bin/python -c "
from pathfinder.app import app
proto = sorted({r.path for r in app.routes if '/proto/' in r.path})
print('proto routes:', proto)
assert proto == ['/proto/{pid}/{slug}', '/proto/{pid}/{slug}/{path:path}'], proto
print('OK')
"
```
Expected: `OK`

- [ ] **Step 8: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/routes/proto_public.py backend/pathfinder/routes/prototypes.py \
        backend/pathfinder/app.py backend/tests/test_routes_prototypes.py
git commit -m "$(cat <<'EOF'
refactor(routes): /proto/* 공개 프록시를 proto_public.py로 분리

인증을 라우터 include 시점에 붙이려면 공개 라우트가 별도 파일에 있어야 한다.
/proto/*는 계정 없는 설문 대상자가 프로토타입을 쓰는 경로라 공개로 남는다.

파일 경계 = 인증 경계. 순수 이동이며 동작 변화는 없다(기존 프록시 테스트가
import 경로만 바뀌어 그대로 통과).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 라우터 전체에 인증 적용 (공개 2개 예외)

**Files:**
- Modify: `backend/pathfinder/app.py` (라우터 include 블록)
- Create: `backend/tests/test_auth_route_coverage.py`

**Interfaces:**
- Consumes: Task 5의 `require_user`, Task 6의 `proto_public.router`
- Produces: 없음 (배선)

**핵심 설계:** 라우트 본문을 하나도 건드리지 않는다.
`app.include_router(r, dependencies=[Depends(require_user)])`로 라우터 단위 적용한다.
공개로 남는 것은 `surveys_public`과 `proto_public` 둘뿐이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

이 테스트는 **회귀 방지 장치**다 — 앞으로 누가 라우터를 추가하면서 인증을 빠뜨리면
여기서 잡힌다.

`backend/tests/test_auth_route_coverage.py`:

```python
# backend/tests/test_auth_route_coverage.py
#
# 인증 커버리지의 회귀 방지 장치. 새 라우터를 추가하면서 인증을 빠뜨리는 것이
# 이 앱에서 가장 값비싼 실수이므로, 라우트 목록 자체를 단정한다.
from __future__ import annotations

from starlette.routing import Route

from pathfinder.app import app

# 무인증으로 열려 있어야 하는 경로 — 정확히 이 둘이다.
#   /survey/{token}  익명 설문 응답 (계정 없는 최종 사용자)
#   /proto/...       프로토타입 라이브 프리뷰 (같은 사용자가 앱을 실제로 써봐야 한다)
PUBLIC_PATHS = {
    "/survey/{token}",
    "/proto/{pid}/{slug}",
    "/proto/{pid}/{slug}/{path:path}",
}


def _app_routes() -> list[Route]:
    """FastAPI가 기본으로 붙이는 /openapi.json·/docs 등을 제외한 실 라우트."""
    builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    return [r for r in app.routes
            if isinstance(r, Route) and r.path not in builtin]


def _has_auth_dependency(route: Route) -> bool:
    """이 라우트의 의존성 트리에 require_user가 있는가."""
    from pathfinder.auth.deps import require_user
    return any(getattr(d, "call", None) is require_user
               for d in route.dependant.dependencies)


def test_every_route_is_either_authenticated_or_explicitly_public():
    unprotected = [r.path for r in _app_routes()
                   if not _has_auth_dependency(r) and r.path not in PUBLIC_PATHS]
    assert unprotected == [], (
        "이 라우트들에 인증이 없다. 의도한 공개라면 PUBLIC_PATHS에 추가하고 "
        f"왜 공개인지 주석을 남길 것: {unprotected}")


def test_public_paths_all_exist():
    # PUBLIC_PATHS가 실제 라우트와 어긋나면(경로 리네임 등) 예외 목록이 조용히
    # 무의미해진다. 이름이 바뀌면 여기서 알아차린다.
    paths = {r.path for r in _app_routes()}
    missing = PUBLIC_PATHS - paths
    assert missing == set(), f"PUBLIC_PATHS references non-existent routes: {missing}"


def test_public_paths_really_have_no_auth_dependency():
    # 반대 방향: 공개여야 하는 경로에 인증이 붙으면 설문/프리뷰가 깨진다.
    wrongly_protected = [r.path for r in _app_routes()
                         if r.path in PUBLIC_PATHS and _has_auth_dependency(r)]
    assert wrongly_protected == [], (
        f"이 경로는 공개여야 한다(계정 없는 사용자가 쓴다): {wrongly_protected}")


def test_admin_routes_require_admin_not_just_user():
    from pathfinder.auth.deps import require_admin
    admin_routes = [r for r in _app_routes() if r.path.startswith("/admin/")]
    assert admin_routes, "관리 라우트가 하나도 없다 — Task 9가 아직인가?"
    for route in admin_routes:
        has_admin = any(getattr(d, "call", None) is require_admin
                        for d in route.dependant.dependencies)
        assert has_admin, f"{route.path} must require the admin role"
```

> `test_admin_routes_require_admin_not_just_user`는 Task 9가 끝날 때까지 실패한다.
> Task 7 완료 시점에는 이 테스트만 실패하는 것이 정상이며, Task 9에서 초록이 된다.
> 나머지 3개는 이 Task에서 통과해야 한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_route_coverage.py -q`
Expected: `test_every_route_is_either_authenticated_or_explicitly_public`가 대량의
미보호 경로를 나열하며 실패

- [ ] **Step 3: `app.py`의 라우터 include 블록을 바꾼다**

`backend/pathfinder/app.py`의 **라우터 등록 블록 전체**를 다음으로 교체한다 —
`from pathfinder.routes import projects, artifacts` 줄부터 파일 끝까지다.

> 행 번호로 찾지 말 것: Task 5가 `cognito_config()`/`jwks_cache()`를, Task 6이
> `proto_public` include를 이미 추가했으므로 원래의 236행은 밀려나 있다. 첫
> `from pathfinder.routes import` 줄을 앵커로 삼는다:
> ```bash
> cd backend && grep -n "from pathfinder.routes import projects" pathfinder/app.py
> ```

```python
# ---- 라우터 등록 ----
#
# 인증은 라우트 본문이 아니라 여기서 붙인다: 라우터 단위 dependencies로 걸면
# 34개 라우트 함수를 하나도 건드리지 않고 전부 보호된다. 인증이 설정되지 않은
# 로컬/테스트에서는 require_user가 전부 통과시킨다(auth/deps.py).
from pathfinder.auth.deps import require_user  # noqa: E402
from fastapi import Depends  # noqa: E402

_AUTH = [Depends(require_user)]

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router, dependencies=_AUTH)
app.include_router(artifacts.router, dependencies=_AUTH)

from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router, dependencies=_AUTH)

from pathfinder.routes import turns  # noqa: E402
app.include_router(turns.router, dependencies=_AUTH)

from pathfinder.routes import discovery  # noqa: E402
app.include_router(discovery.router, dependencies=_AUTH)

from pathfinder.routes import history  # noqa: E402
app.include_router(history.router, dependencies=_AUTH)

from pathfinder.routes import uploads  # noqa: E402
app.include_router(uploads.router, dependencies=_AUTH)

from pathfinder.routes import prototypes  # noqa: E402
app.include_router(prototypes.router, dependencies=_AUTH)

from pathfinder.routes import surveys  # noqa: E402
app.include_router(surveys.router, dependencies=_AUTH)

from pathfinder.routes import admin_users  # noqa: E402
app.include_router(admin_users.router, dependencies=_AUTH)

# ---- 공개(무인증) 라우터 — 정확히 둘 ----
#
# 여기에 라우터를 추가하는 것은 인터넷에 공개하는 것과 같다. 두 경로 모두 계정이
# 없는 최종 사용자를 위한 것이다: 설문 링크를 받아 응답하고(surveys_public),
# 평가 대상 프로토타입을 실제로 써본다(proto_public).
# tests/test_auth_route_coverage.py가 이 목록을 강제한다.
from pathfinder.routes import surveys_public  # noqa: E402
app.include_router(surveys_public.router)

from pathfinder.routes import proto_public  # noqa: E402
app.include_router(proto_public.router)
```

> `admin_users` include는 Task 9가 그 모듈을 만들 때까지 `ImportError`를 낸다.
> **지금은 그 두 줄을 주석 처리하고 Task 9에서 살린다:**
> ```python
> # from pathfinder.routes import admin_users  # noqa: E402  ← Task 9에서 활성화
> # app.include_router(admin_users.router, dependencies=_AUTH)
> ```
> 그리고 `admin_users`의 라우터는 자체적으로 `require_admin`을 갖는다(Task 9) —
> 여기서 `_AUTH`를 겹쳐 붙여도 무해하다(`require_admin`이 `require_user`에
> 의존하므로 같은 검증이 캐시된다).

- [ ] **Step 4: 통과를 확인한다 (admin 테스트 1개 제외)**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/test_auth_route_coverage.py -q \
  --deselect tests/test_auth_route_coverage.py::test_admin_routes_require_admin_not_just_user
```
Expected: 3 passed

- [ ] **Step 5: 기존 스위트 전체가 그대로 통과하는지 확인한다**

이것이 이 Task의 핵심 검증이다 — 바이패스가 실제로 동작한다는 증거다.

Run: `cd backend && .venv/bin/python -m pytest -q -k "not test_admin_routes_require_admin"`
Expected: 기존 테스트 전부 통과 (인증을 켜지 않았으므로 아무것도 깨지지 않는다)

- [ ] **Step 6: 인증을 켠 상태에서 401이 나오는지 스모크 테스트한다**

```bash
cd backend && .venv/bin/python -c "
import os
os.environ['PATHFINDER_COGNITO_USER_POOL_ID'] = 'ap-northeast-2_FAKE'
os.environ['PATHFINDER_COGNITO_CLIENT_ID'] = 'fake-client'
from fastapi.testclient import TestClient
from pathfinder.app import app
c = TestClient(app)
r = c.get('/projects')
assert r.status_code == 401, f'expected 401, got {r.status_code}'
assert r.headers.get('www-authenticate') == 'Bearer'
# 공개 경로는 인증을 켠 상태에서도 401이 아니어야 한다(404/502 등 다른 이유는 무방).
r2 = c.get('/survey/nope')
assert r2.status_code != 401, f'/survey must stay public, got {r2.status_code}'
r3 = c.get('/proto/p/s/index.html')
assert r3.status_code != 401, f'/proto must stay public, got {r3.status_code}'
print('OK  auth on: protected=401, public routes unaffected')
"
```
Expected: `OK  auth on: protected=401, public routes unaffected`

- [ ] **Step 7: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/app.py backend/tests/test_auth_route_coverage.py
git commit -m "$(cat <<'EOF'
feat(auth): 라우터 단위로 인증 적용 — 공개 경로는 정확히 둘

라우트 함수 34개를 건드리지 않고 include 시점 dependencies로 보호한다.
공개로 남는 것은 surveys_public(익명 설문)과 proto_public(프로토타입 프리뷰)뿐.

test_auth_route_coverage.py가 이 경계를 강제한다: 인증도 없고 공개 목록에도
없는 라우트가 생기면 실패한다. 반대 방향(공개여야 할 경로에 인증이 붙는 것)도
검사한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Cognito Admin API 래퍼

**Files:**
- Create: `backend/pathfinder/auth/cognito.py`
- Create: `backend/tests/test_auth_cognito.py`

**Interfaces:**
- Consumes: Task 4의 `Role`
- Produces:
  - `class CognitoError(Exception)` — `code: str` 속성 (Cognito 오류 코드 원문)
  - `@dataclass class ManagedUser: username: str; email: str; role: Role | None; status: str; enabled: bool; created_at: str`
  - `class CognitoAdmin`:
    - `__init__(self, client, user_pool_id: str)`
    - `def list_users(self) -> list[ManagedUser]`
    - `def create_user(self, email: str) -> str` — 생성된 username 반환
    - `def set_temp_password(self, username: str, password: str) -> None`
    - `def set_group(self, username: str, role: Role) -> None` — 기존 그룹 제거 후 추가
    - `def delete_user(self, username: str) -> None`
    - `def set_enabled(self, username: str, enabled: bool) -> None`
    - `def groups_of(self, username: str) -> list[str]`
    - `def admin_count(self) -> int` — admin 그룹 멤버 수
  - `def generate_temp_password(length: int = 16) -> str`

**왜 라우트에서 분리하는가:** boto3 호출을 한 곳에 모으면 라우트는 정책(마지막 관리자
보호, 부분 실패 롤백)만 다루고, 이 파일은 Stubber로 독립 테스트된다. 기존 코드가
`s3store.py`로 S3 호출을 분리한 것과 같은 구조다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_auth_cognito.py`:

```python
# backend/tests/test_auth_cognito.py
#
# botocore Stubber로 실 Cognito 없이 래퍼를 시험한다. Stubber는 파라미터가
# 예상과 정확히 일치할 때만 응답을 내놓으므로, "무엇을 어떻게 호출하는가"가
# 그대로 단정된다.
from __future__ import annotations

import re

import boto3
import pytest
from botocore.stub import ANY, Stubber

from pathfinder.auth.cognito import (CognitoAdmin, CognitoError, ManagedUser,
                                     generate_temp_password)

POOL = "ap-northeast-2_TEST123"


@pytest.fixture()
def admin():
    client = boto3.client("cognito-idp", region_name="ap-northeast-2",
                          aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.activate()
    yield CognitoAdmin(client, POOL), stub
    stub.deactivate()


def _user(username: str, email: str, status="CONFIRMED", enabled=True):
    from datetime import datetime, timezone
    return {
        "Username": username,
        "Attributes": [{"Name": "email", "Value": email}],
        "UserStatus": status,
        "Enabled": enabled,
        "UserCreateDate": datetime(2026, 7, 25, tzinfo=timezone.utc),
    }


# ---- 비밀번호 생성 ----

def test_generated_password_satisfies_the_pool_policy():
    # 정책은 8자+ 대/소/숫자/기호. 생성기가 정책을 못 맞추면 사용자는 이미
    # 만들어진 뒤 InvalidPasswordException이 나므로 반드시 만족해야 한다.
    for _ in range(50):
        pw = generate_temp_password()
        assert len(pw) == 16
        assert re.search(r"[a-z]", pw), pw
        assert re.search(r"[A-Z]", pw), pw
        assert re.search(r"[0-9]", pw), pw
        assert re.search(r"[!@#$%^&*_\-+=?]", pw), pw


def test_generated_passwords_are_not_repeated():
    assert len({generate_temp_password() for _ in range(50)}) == 50


# ---- 목록 ----

def test_list_users_maps_attributes_and_groups(admin):
    a, stub = admin
    stub.add_response(
        "list_users",
        {"Users": [_user("admin@pathfinder.local", "admin@pathfinder.local"),
                   _user("pm@pathfinder.local", "pm@pathfinder.local")]},
        {"UserPoolId": POOL, "Limit": 60},
    )
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "admin"}]},
                      {"UserPoolId": POOL, "Username": "admin@pathfinder.local"})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "pm@pathfinder.local"})
    users = a.list_users()
    assert [u.email for u in users] == ["admin@pathfinder.local", "pm@pathfinder.local"]
    assert [u.role for u in users] == ["admin", "pm"]
    assert users[0].status == "CONFIRMED" and users[0].enabled is True
    assert users[0].created_at.startswith("2026-07-25")


def test_list_users_follows_pagination(admin):
    a, stub = admin
    stub.add_response("list_users",
                      {"Users": [_user("a@x.io", "a@x.io")], "PaginationToken": "t1"},
                      {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "a@x.io"})
    stub.add_response("list_users", {"Users": [_user("b@x.io", "b@x.io")]},
                      {"UserPoolId": POOL, "Limit": 60, "PaginationToken": "t1"})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "b@x.io"})
    assert [u.email for u in a.list_users()] == ["a@x.io", "b@x.io"]


def test_user_with_no_group_has_role_none(admin):
    # 그룹 배정 전에 실패한 반쯤 만들어진 계정을 화면에서 알아볼 수 있어야 한다.
    a, stub = admin
    stub.add_response("list_users", {"Users": [_user("x@x.io", "x@x.io")]},
                      {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": []},
                      {"UserPoolId": POOL, "Username": "x@x.io"})
    assert a.list_users()[0].role is None


def test_missing_email_attribute_falls_back_to_username(admin):
    a, stub = admin
    raw = _user("legacy-user", "unused")
    raw["Attributes"] = []
    stub.add_response("list_users", {"Users": [raw]}, {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "legacy-user"})
    assert a.list_users()[0].email == "legacy-user"


# ---- 생성 ----

def test_create_user_suppresses_email_and_marks_it_verified(admin):
    a, stub = admin
    # email_verified=true는 선택이 아니다 — alias(email) 사인인의 조건이다.
    stub.add_response(
        "admin_create_user",
        {"User": _user("new@x.io", "new@x.io", status="FORCE_CHANGE_PASSWORD")},
        {"UserPoolId": POOL, "Username": "new@x.io", "MessageAction": "SUPPRESS",
         "UserAttributes": [{"Name": "email", "Value": "new@x.io"},
                            {"Name": "email_verified", "Value": "true"}]},
    )
    assert a.create_user("new@x.io") == "new@x.io"


def test_duplicate_email_raises_cognito_error_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_create_user", service_error_code="UsernameExistsException")
    with pytest.raises(CognitoError) as exc:
        a.create_user("dup@x.io")
    assert exc.value.code == "UsernameExistsException"


def test_alias_exists_is_also_surfaced_by_code(admin):
    # 이메일이 다른 계정의 alias로 이미 쓰이는 경우.
    a, stub = admin
    stub.add_client_error("admin_create_user", service_error_code="AliasExistsException")
    with pytest.raises(CognitoError) as exc:
        a.create_user("alias@x.io")
    assert exc.value.code == "AliasExistsException"


# ---- 비밀번호 ----

def test_set_temp_password_is_not_permanent(admin):
    # Permanent=False여야 첫 로그인에서 사용자가 직접 바꾼다(초대 흐름).
    a, stub = admin
    stub.add_response("admin_set_user_password", {},
                      {"UserPoolId": POOL, "Username": "u@x.io",
                       "Password": "Tmp!23456789abcd", "Permanent": False})
    a.set_temp_password("u@x.io", "Tmp!23456789abcd")


def test_invalid_password_raises_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_set_user_password",
                         service_error_code="InvalidPasswordException")
    with pytest.raises(CognitoError) as exc:
        a.set_temp_password("u@x.io", "weak")
    assert exc.value.code == "InvalidPasswordException"


# ---- 그룹(역할) ----

def test_set_group_removes_existing_roles_before_adding(admin):
    # 역할 교체는 "추가"가 아니라 "교체"다. 제거를 빠뜨리면 사용자가 두 그룹에
    # 속해 강등이 무효가 된다(verifier가 admin을 우선하므로).
    a, stub = admin
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "admin"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    stub.add_response("admin_remove_user_from_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "admin"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "pm"})
    a.set_group("u@x.io", "pm")


def test_set_group_leaves_unrelated_groups_alone(admin):
    # admin/pm이 아닌 그룹은 우리 관심사가 아니다 — 건드리지 않는다.
    a, stub = admin
    stub.add_response("admin_list_groups_for_user",
                      {"Groups": [{"GroupName": "some-other-group"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "admin"})
    a.set_group("u@x.io", "admin")


def test_set_group_is_a_noop_add_when_already_correct(admin):
    # 이미 맞는 그룹이면 제거하지 않고 추가만 한다(멱등).
    a, stub = admin
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "pm"})
    a.set_group("u@x.io", "pm")


# ---- 활성/비활성/삭제 ----

def test_disable_and_enable(admin):
    a, stub = admin
    stub.add_response("admin_disable_user", {}, {"UserPoolId": POOL, "Username": "u@x.io"})
    a.set_enabled("u@x.io", False)
    stub.add_response("admin_enable_user", {}, {"UserPoolId": POOL, "Username": "u@x.io"})
    a.set_enabled("u@x.io", True)


def test_delete_user(admin):
    a, stub = admin
    stub.add_response("admin_delete_user", {}, {"UserPoolId": POOL, "Username": "u@x.io"})
    a.delete_user("u@x.io")


def test_unknown_user_raises_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_delete_user", service_error_code="UserNotFoundException")
    with pytest.raises(CognitoError) as exc:
        a.delete_user("ghost@x.io")
    assert exc.value.code == "UserNotFoundException"


# ---- 관리자 수 (마지막 관리자 보호의 입력) ----

def test_admin_count_reads_the_admin_group(admin):
    a, stub = admin
    stub.add_response("list_users_in_group",
                      {"Users": [_user("a@x.io", "a@x.io"), _user("b@x.io", "b@x.io")]},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60})
    assert a.admin_count() == 2


def test_admin_count_follows_pagination(admin):
    a, stub = admin
    stub.add_response("list_users_in_group",
                      {"Users": [_user("a@x.io", "a@x.io")], "NextToken": "n1"},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60})
    stub.add_response("list_users_in_group", {"Users": [_user("b@x.io", "b@x.io")]},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60,
                       "NextToken": "n1"})
    assert a.admin_count() == 2


def test_groups_of_returns_names(admin):
    a, stub = admin
    stub.add_response("admin_list_groups_for_user",
                      {"Groups": [{"GroupName": "admin"}, {"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    assert a.groups_of("u@x.io") == ["admin", "pm"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_cognito.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.auth.cognito'`

- [ ] **Step 3: 구현을 쓴다**

`backend/pathfinder/auth/cognito.py`:

```python
# backend/pathfinder/auth/cognito.py
#
# Cognito Admin* API 래퍼. boto3 호출을 라우트에서 분리하는 이유는 s3store.py와
# 같다: 라우트는 정책(마지막 관리자 보호, 부분 실패 롤백)만 다루고, 이 파일은
# Stubber로 독립 검증된다.
#
# 이 풀은 AliasAttributes(email)이므로 Username을 호출자가 정한다 — 우리는
# 이메일을 그대로 Username으로 쓴다. 그래서 모든 Admin* 호출이 이메일로 결정적이다.
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from botocore.exceptions import ClientError

from pathfinder.auth.models import ROLE_ADMIN, ROLE_PM, Role

_log = logging.getLogger(__name__)

# 한 번에 가져오는 사용자 수. Cognito의 상한은 60이다.
_PAGE = 60

# 우리가 관리하는 역할 그룹. 이 밖의 그룹은 건드리지 않는다.
_ROLE_GROUPS = (ROLE_ADMIN, ROLE_PM)

# 임시 비밀번호 문자군. 풀 정책(8자+ 대/소/숫자/기호)을 만족시키기 위해 각 군에서
# 최소 1자를 보장한다. 혼동하기 쉬운 문자(0/O, 1/l/I)는 제외했다 — 임시 비밀번호는
# 사람이 메신저로 옮겨 적는 값이다.
_LOWER = "abcdefghijkmnopqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_SYMBOLS = "!@#$%^&*_-+=?"


class CognitoError(Exception):
    """Cognito가 거부했다. `code`는 원문 오류 코드(라우트가 상태코드로 번역한다)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class ManagedUser:
    username: str      # Admin* API 호출에 쓰는 값
    email: str         # 화면에 보여주는 값
    role: Role | None  # 그룹 미배정(반쯤 만들어진 계정)이면 None
    status: str        # CONFIRMED / FORCE_CHANGE_PASSWORD / ...
    enabled: bool
    created_at: str    # ISO8601


def generate_temp_password(length: int = 16) -> str:
    """정책을 만족하는 임시 비밀번호.

    각 문자군에서 1자를 먼저 고른 뒤 나머지를 채우고 섞는다. 무작위 문자열을
    뽑아 정책 통과를 기대하는 방식은 드물게 실패하고, 그 실패는 사용자가 이미
    생성된 뒤에 InvalidPasswordException으로 나타난다.
    """
    pools = (_LOWER, _UPPER, _DIGITS, _SYMBOLS)
    chars = [secrets.choice(p) for p in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(pools))]
    # secrets 기반 Fisher-Yates — random.shuffle은 암호학적으로 안전하지 않다.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


class CognitoAdmin:
    def __init__(self, client, user_pool_id: str) -> None:
        self._c = client
        self._pool = user_pool_id

    # ---- 내부 ----

    def _call(self, name: str, **params):
        try:
            return getattr(self._c, name)(UserPoolId=self._pool, **params)
        except ClientError as exc:
            err = exc.response.get("Error", {})
            code = err.get("Code", "Unknown")
            raise CognitoError(code, err.get("Message", str(exc))) from exc

    @staticmethod
    def _email_of(raw: dict) -> str:
        for attr in raw.get("Attributes", []):
            if attr.get("Name") == "email":
                return attr.get("Value", "")
        # 이메일 속성이 없는 계정(수동 생성 등)도 화면에서 식별 가능해야 한다.
        return raw.get("Username", "")

    @staticmethod
    def _role_of(groups: list[str]) -> Role | None:
        # verifier와 같은 우선순위: 두 그룹에 다 속하면 admin으로 본다.
        if ROLE_ADMIN in groups:
            return ROLE_ADMIN
        if ROLE_PM in groups:
            return ROLE_PM
        return None

    # ---- 조회 ----

    def groups_of(self, username: str) -> list[str]:
        resp = self._call("admin_list_groups_for_user", Username=username)
        return [g["GroupName"] for g in resp.get("Groups", [])]

    def list_users(self) -> list[ManagedUser]:
        users: list[ManagedUser] = []
        token: str | None = None
        while True:
            params = {"Limit": _PAGE}
            if token:
                params["PaginationToken"] = token
            resp = self._call("list_users", **params)
            for raw in resp.get("Users", []):
                username = raw.get("Username", "")
                created = raw.get("UserCreateDate")
                users.append(ManagedUser(
                    username=username,
                    email=self._email_of(raw),
                    role=self._role_of(self.groups_of(username)),
                    status=raw.get("UserStatus", ""),
                    enabled=bool(raw.get("Enabled", True)),
                    created_at=created.isoformat() if created else "",
                ))
            token = resp.get("PaginationToken")
            if not token:
                return users

    def admin_count(self) -> int:
        """admin 그룹 멤버 수 — 마지막 관리자 보호의 입력."""
        total = 0
        token: str | None = None
        while True:
            params = {"GroupName": ROLE_ADMIN, "Limit": _PAGE}
            if token:
                params["NextToken"] = token
            resp = self._call("list_users_in_group", **params)
            total += len(resp.get("Users", []))
            token = resp.get("NextToken")
            if not token:
                return total

    # ---- 변경 ----

    def create_user(self, email: str) -> str:
        """사용자를 만들고 Username을 반환한다.

        MessageAction=SUPPRESS: 이 앱은 메일을 보내지 않는다(초대는 관리 페이지가
        임시 비밀번호를 화면에 1회 보여준다).
        email_verified=true: 선택이 아니라 alias(email) 사인인의 조건이다.
        """
        resp = self._call(
            "admin_create_user",
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email},
                            {"Name": "email_verified", "Value": "true"}],
        )
        return resp.get("User", {}).get("Username", email)

    def set_temp_password(self, username: str, password: str) -> None:
        """임시 비밀번호. Permanent=False라 첫 로그인에서 사용자가 직접 바꾼다."""
        self._call("admin_set_user_password", Username=username,
                   Password=password, Permanent=False)

    def set_group(self, username: str, role: Role) -> None:
        """역할을 교체한다 — 추가가 아니라 교체다.

        기존 역할 그룹을 지우지 않으면 사용자가 admin과 pm에 동시에 속해
        강등이 무효가 된다(verifier가 admin을 우선한다). admin/pm 밖의 그룹은
        우리 관심사가 아니므로 건드리지 않는다.
        """
        for existing in self.groups_of(username):
            if existing in _ROLE_GROUPS and existing != role:
                self._call("admin_remove_user_from_group", Username=username,
                           GroupName=existing)
        self._call("admin_add_user_to_group", Username=username, GroupName=role)

    def set_enabled(self, username: str, enabled: bool) -> None:
        action = "admin_enable_user" if enabled else "admin_disable_user"
        self._call(action, Username=username)

    def delete_user(self, username: str) -> None:
        self._call("admin_delete_user", Username=username)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_cognito.py -q`
Expected: 20 passed

> `secrets.randbelow`는 이 venv(Python 3.11)에 존재함을 확인했다 — 별도 대체가
> 필요 없다. `random.shuffle`을 쓰지 않는 이유는 그것이 Mersenne Twister 기반으로
> 암호학적으로 안전하지 않기 때문이다.

- [ ] **Step 5: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/auth/cognito.py backend/tests/test_auth_cognito.py
git commit -m "$(cat <<'EOF'
feat(auth): Cognito Admin API 래퍼 + 임시 비밀번호 생성기

boto3 호출을 라우트에서 분리한다(s3store.py와 같은 구조) — 라우트는 정책만
다루고 이 파일은 Stubber로 독립 검증된다.

set_group은 추가가 아니라 교체다: 기존 역할 그룹을 지우지 않으면 사용자가
admin과 pm에 동시에 속해 강등이 무효가 된다(verifier가 admin을 우선한다).

임시 비밀번호는 각 문자군에서 1자를 보장한 뒤 채운다 — 무작위 후 정책 통과를
기대하면 드물게 실패하고, 그 실패는 사용자가 이미 생성된 뒤에 드러난다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `/admin/users` 라우트 7개 + 마지막 관리자 보호

**Files:**
- Create: `backend/pathfinder/routes/admin_users.py`
- Create: `backend/tests/test_routes_admin_users.py`
- Modify: `backend/pathfinder/app.py` (Task 7에서 주석 처리한 include 2줄 활성화 + 팩토리)

**Interfaces:**
- Consumes: Task 5의 `require_admin`, Task 8의 `CognitoAdmin`·`CognitoError`·`generate_temp_password`
- Produces:
  - `admin_users.router` — 7개 라우트
  - `app.py`의 `def cognito_admin()` — `CognitoAdmin` 팩토리 (monkeypatchable)

**응답 스키마 (프론트가 이 형태에 의존한다):**

```
GET  /admin/users                        → {"users": [{username, email, role, status, enabled, created_at}]}
POST /admin/users {email, role}          → 201 {"username", "email", "role", "temp_password"}
POST /admin/users/{u}/reset-password     → 200 {"username", "temp_password"}
PUT  /admin/users/{u}/role {role}        → 200 {"username", "role"}
POST /admin/users/{u}/disable            → 204
POST /admin/users/{u}/enable             → 204
DEL  /admin/users/{u}                    → 204
```

**정책 (라우트가 책임지는 것):**

1. **자기 자신**의 역할 강등 / 비활성화 / 삭제 → 400
2. **admin이 1명뿐일 때** 그 계정의 강등 / 비활성화 / 삭제 → 400
3. 초대 중간 실패 → 방금 만든 사용자를 지우고 500
4. Cognito 오류 코드 → 상태코드 번역: `UsernameExists`/`AliasExists` → 409,
   `UserNotFound` → 404, `InvalidPassword` → 500, 그 외 → 502

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_routes_admin_users.py`:

```python
# backend/tests/test_routes_admin_users.py
#
# 라우트 계층의 책임만 시험한다: 정책(마지막 관리자 보호, 부분 실패 롤백)과
# 오류 코드 번역. Cognito 호출 자체는 test_auth_cognito.py가 Stubber로 검증하므로
# 여기서는 CognitoAdmin을 가짜로 갈아끼운다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.auth.cognito import CognitoError, ManagedUser
from pathfinder.auth.deps import require_admin, require_user
from pathfinder.auth.models import Principal

ADMIN_EMAIL = "admin@pathfinder.local"
PM_EMAIL = "pm@pathfinder.local"


class FakeCognito:
    """CognitoAdmin의 인메모리 대역. 호출 순서를 calls에 기록한다."""

    def __init__(self) -> None:
        self.users: dict[str, ManagedUser] = {}
        self.calls: list[tuple] = []
        self.fail_on: dict[str, CognitoError] = {}
        self.deleted: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        if op in self.fail_on:
            raise self.fail_on[op]

    def add(self, email: str, role: str | None = "pm", enabled: bool = True,
            status: str = "CONFIRMED") -> None:
        self.users[email] = ManagedUser(username=email, email=email, role=role,
                                        status=status, enabled=enabled,
                                        created_at="2026-07-25T00:00:00+00:00")

    def list_users(self):
        self.calls.append(("list_users",))
        self._maybe_fail("list_users")
        return list(self.users.values())

    def admin_count(self) -> int:
        return sum(1 for u in self.users.values() if u.role == "admin")

    def groups_of(self, username: str):
        u = self.users.get(username)
        return [u.role] if u and u.role else []

    def create_user(self, email: str) -> str:
        self.calls.append(("create_user", email))
        self._maybe_fail("create_user")
        self.add(email, role=None, status="FORCE_CHANGE_PASSWORD")
        return email

    def set_temp_password(self, username: str, password: str) -> None:
        self.calls.append(("set_temp_password", username, password))
        self._maybe_fail("set_temp_password")

    def set_group(self, username: str, role: str) -> None:
        self.calls.append(("set_group", username, role))
        self._maybe_fail("set_group")
        if username in self.users:
            self.users[username].role = role

    def set_enabled(self, username: str, enabled: bool) -> None:
        self.calls.append(("set_enabled", username, enabled))
        self._maybe_fail("set_enabled")
        if username in self.users:
            self.users[username].enabled = enabled

    def delete_user(self, username: str) -> None:
        self.calls.append(("delete_user", username))
        self.deleted.append(username)
        self._maybe_fail("delete_user")
        self.users.pop(username, None)


@pytest.fixture()
def env(monkeypatch):
    """가짜 Cognito + '나는 admin@pathfinder.local' 라는 요청자."""
    fake = FakeCognito()
    fake.add(ADMIN_EMAIL, role="admin")
    fake.add(PM_EMAIL, role="pm")
    monkeypatch.setattr(app_module, "cognito_admin", lambda: fake)

    me = Principal(username=ADMIN_EMAIL, sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    yield fake
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


# ---- 목록 ----

def test_list_returns_users_with_role_and_status(env, client):
    body = client.get("/admin/users").json()
    emails = {u["email"] for u in body["users"]}
    assert emails == {ADMIN_EMAIL, PM_EMAIL}
    admin_row = next(u for u in body["users"] if u["email"] == ADMIN_EMAIL)
    assert admin_row["role"] == "admin"
    assert admin_row["enabled"] is True
    assert admin_row["status"] == "CONFIRMED"
    # 화면은 email을 보여주고 액션은 username을 보낸다 — 둘 다 나와야 한다.
    assert admin_row["username"] == ADMIN_EMAIL


# ---- 초대 ----

def test_invite_creates_sets_password_and_assigns_group_in_order(env, client):
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@x.io"
    assert body["role"] == "pm"
    # 임시 비밀번호는 응답에 1회만 실린다 — 서버는 저장하지 않는다.
    assert len(body["temp_password"]) == 16
    ops = [c[0] for c in env.calls]
    assert ops == ["create_user", "set_temp_password", "set_group"], ops


def test_invite_rejects_an_unknown_role(env, client):
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "superuser"})
    assert r.status_code == 422
    assert env.calls == [], "잘못된 역할로 사용자를 만들어서는 안 된다"


def test_invite_rejects_a_malformed_email(env, client):
    r = client.post("/admin/users", json={"email": "not-an-email", "role": "pm"})
    assert r.status_code == 422
    assert env.calls == []


def test_duplicate_email_is_409(env, client):
    env.fail_on["create_user"] = CognitoError("UsernameExistsException", "exists")
    r = client.post("/admin/users", json={"email": PM_EMAIL, "role": "pm"})
    assert r.status_code == 409


def test_alias_exists_is_also_409(env, client):
    env.fail_on["create_user"] = CognitoError("AliasExistsException", "alias")
    r = client.post("/admin/users", json={"email": "x@x.io", "role": "pm"})
    assert r.status_code == 409


def test_password_failure_rolls_back_the_created_user(env, client):
    # 반쯤 만들어진 계정(비밀번호 없음 / 그룹 없음)을 남기지 않는다 —
    # projects.py의 매니페스트 실패 롤백과 같은 규율.
    env.fail_on["set_temp_password"] = CognitoError("InvalidPasswordException", "weak")
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 500
    assert env.deleted == ["new@x.io"], f"rollback did not delete the user: {env.deleted}"


def test_group_failure_rolls_back_the_created_user(env, client):
    env.fail_on["set_group"] = CognitoError("ResourceNotFoundException", "no group")
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 500
    assert env.deleted == ["new@x.io"]


def test_rollback_failure_still_returns_500(env, client):
    # 롤백까지 실패하면 우리가 할 수 있는 것이 없다 — 500을 내고 로그에 남긴다.
    env.fail_on["set_group"] = CognitoError("ResourceNotFoundException", "no group")
    env.fail_on["delete_user"] = CognitoError("UserNotFoundException", "gone")
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 500


# ---- 비밀번호 재설정 ----

def test_reset_password_returns_a_new_temp_password(env, client):
    r = client.post(f"/admin/users/{PM_EMAIL}/reset-password")
    assert r.status_code == 200
    assert len(r.json()["temp_password"]) == 16
    assert [c[0] for c in env.calls] == ["set_temp_password"]


def test_reset_password_on_unknown_user_is_404(env, client):
    env.fail_on["set_temp_password"] = CognitoError("UserNotFoundException", "gone")
    r = client.post("/admin/users/ghost@x.io/reset-password")
    assert r.status_code == 404


# ---- 역할 변경 ----

def test_role_change_replaces_the_group(env, client):
    r = client.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json() == {"username": PM_EMAIL, "role": "admin"}
    assert ("set_group", PM_EMAIL, "admin") in env.calls


def test_cannot_demote_yourself(env, client):
    # 이게 없으면 관리자가 스스로를 잠가내고 복구 경로가 AWS 콘솔밖에 안 남는다.
    r = client.put(f"/admin/users/{ADMIN_EMAIL}/role", json={"role": "pm"})
    assert r.status_code == 400
    assert "set_group" not in [c[0] for c in env.calls]


def test_cannot_demote_the_last_admin(env, client):
    # 요청자가 아닌 다른 계정이지만 유일한 admin인 경우.
    env.users.pop(ADMIN_EMAIL)
    env.add("other-admin@x.io", role="admin")
    r = client.put("/admin/users/other-admin@x.io/role", json={"role": "pm"})
    assert r.status_code == 400


def test_can_demote_an_admin_when_another_admin_remains(env, client):
    env.add("second-admin@x.io", role="admin")
    r = client.put("/admin/users/second-admin@x.io/role", json={"role": "pm"})
    assert r.status_code == 200


def test_promoting_to_admin_is_always_allowed(env, client):
    r = client.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "admin"})
    assert r.status_code == 200


# ---- 비활성 / 활성 ----

def test_disable_a_pm(env, client):
    assert client.post(f"/admin/users/{PM_EMAIL}/disable").status_code == 204
    assert ("set_enabled", PM_EMAIL, False) in env.calls


def test_cannot_disable_yourself(env, client):
    r = client.post(f"/admin/users/{ADMIN_EMAIL}/disable")
    assert r.status_code == 400
    assert "set_enabled" not in [c[0] for c in env.calls]


def test_cannot_disable_the_last_admin(env, client):
    env.users.pop(ADMIN_EMAIL)
    env.add("other-admin@x.io", role="admin")
    assert client.post("/admin/users/other-admin@x.io/disable").status_code == 400


def test_enable_is_never_blocked(env, client):
    # 활성화는 권한을 넓히는 방향이므로 마지막 관리자 보호와 무관하다.
    env.add("disabled@x.io", role="admin", enabled=False)
    assert client.post("/admin/users/disabled@x.io/enable").status_code == 204


def test_enabling_yourself_is_allowed(env, client):
    assert client.post(f"/admin/users/{ADMIN_EMAIL}/enable").status_code == 204


# ---- 삭제 ----

def test_delete_a_pm(env, client):
    assert client.delete(f"/admin/users/{PM_EMAIL}").status_code == 204
    assert PM_EMAIL not in env.users


def test_cannot_delete_yourself(env, client):
    r = client.delete(f"/admin/users/{ADMIN_EMAIL}")
    assert r.status_code == 400
    assert ADMIN_EMAIL in env.users


def test_cannot_delete_the_last_admin(env, client):
    env.users.pop(ADMIN_EMAIL)
    env.add("other-admin@x.io", role="admin")
    assert client.delete("/admin/users/other-admin@x.io").status_code == 400


def test_delete_unknown_user_is_404(env, client):
    env.fail_on["delete_user"] = CognitoError("UserNotFoundException", "gone")
    assert client.delete("/admin/users/ghost@x.io").status_code == 404


# ---- 오류 번역 ----

def test_unexpected_cognito_error_is_502(env, client):
    env.fail_on["list_users"] = CognitoError("InternalErrorException", "boom")
    r = client.get("/admin/users")
    assert r.status_code == 502
    # 내부 세부사항을 사용자에게 노출하지 않는다.
    assert "InternalErrorException" not in r.text


# ---- 권한 (require_admin 우회 불가) ----

def test_pm_cannot_reach_admin_routes(monkeypatch, client):
    # dependency_overrides 없이 실제 require_admin을 통과시켜본다.
    fake = FakeCognito()
    monkeypatch.setattr(app_module, "cognito_admin", lambda: fake)
    monkeypatch.setattr(app_module, "cognito_config", lambda: {
        "region": "ap-northeast-2", "user_pool_id": "p", "client_id": "c"})

    import pathfinder.auth.deps as deps_module

    async def fake_verify(token, **kwargs):
        return Principal(username=PM_EMAIL, sub="s-pm", role="pm")

    monkeypatch.setattr(deps_module, "verify_access_token", fake_verify)
    monkeypatch.setattr(app_module, "jwks_cache", lambda: object())
    r = client.get("/admin/users", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403
    assert fake.calls == [], "pm의 요청이 Cognito까지 도달해서는 안 된다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_admin_users.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.routes.admin_users'`

- [ ] **Step 3: `app.py`에 `cognito_admin()` 팩토리를 추가한다**

Task 5에서 넣은 `def jwks_cache():` 블록 **바로 뒤**에 추가한다:

```python
def cognito_admin():
    """CognitoAdmin 팩토리 (monkeypatchable in tests).

    싱글턴으로 두지 않는 이유: boto3 클라이언트는 스레드 세이프하지만, 테스트가
    요청마다 가짜로 갈아끼울 수 있어야 하고 생성 비용은 무시할 만하다.
    """
    from pathfinder.auth.cognito import CognitoAdmin
    cfg = cognito_config()
    if cfg is None:
        raise RuntimeError(
            "user management requires PATHFINDER_COGNITO_USER_POOL_ID / "
            "PATHFINDER_COGNITO_CLIENT_ID")
    client = boto3.client("cognito-idp", region_name=cfg["region"])
    return CognitoAdmin(client, cfg["user_pool_id"])
```

- [ ] **Step 4: `admin_users.py`를 구현한다**

`backend/pathfinder/routes/admin_users.py`:

```python
# backend/pathfinder/routes/admin_users.py — 사용자 관리 (admin 전용).
#
# 신규 가입은 초대로만 가능하다(풀은 AllowAdminCreateUserOnly). 이 라우터가 그
# 초대 창구다. Cognito 호출 자체는 auth/cognito.py가 담당하고, 여기서는 정책만
# 다룬다:
#
#   1) 마지막 관리자 보호 — 자기 자신 또는 유일한 admin의 강등·비활성·삭제를 막는다.
#      없으면 관리자가 스스로를 잠가내고 복구 경로가 AWS 콘솔밖에 남지 않는다.
#   2) 초대의 부분 실패 롤백 — 반쯤 만들어진 계정(그룹 없음 = 역할 없음)을
#      남기지 않는다. projects.py의 매니페스트 실패 롤백과 같은 규율.
#   3) Cognito 오류 코드 → HTTP 상태코드 번역. 원문 코드는 로그에만 남긴다.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from starlette.responses import Response

from pathfinder.auth.cognito import CognitoError, generate_temp_password
from pathfinder.auth.deps import require_admin
from pathfinder.auth.models import Principal, Role

_log = logging.getLogger(__name__)

# 라우터 전체가 admin 전용이다 — 라우트마다 붙이는 것을 잊을 여지를 없앤다.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Cognito 오류 코드 → HTTP 상태. 목록에 없는 코드는 502(업스트림 장애)로 본다.
_ERROR_STATUS = {
    "UsernameExistsException": 409,
    "AliasExistsException": 409,
    "UserNotFoundException": 404,
    "ResourceNotFoundException": 404,
    "InvalidParameterException": 400,
    "NotAuthorizedException": 403,
    "TooManyRequestsException": 429,
}

_ERROR_DETAIL = {
    409: "이미 등록된 이메일입니다.",
    404: "사용자를 찾을 수 없습니다.",
    400: "요청이 올바르지 않습니다.",
    403: "권한이 없습니다.",
    429: "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
}


def _http_error(exc: CognitoError) -> HTTPException:
    """Cognito 오류를 사용자에게 보여줄 수 있는 형태로 바꾼다.

    원문 코드는 내부 정보이므로 로그에만 남긴다.
    """
    status = _ERROR_STATUS.get(exc.code, 502)
    _log.warning("cognito call failed (%s) -> %d", exc.code, status)
    return HTTPException(status_code=status,
                         detail=_ERROR_DETAIL.get(status, "사용자 관리 요청이 실패했습니다."))


class InviteBody(BaseModel):
    email: EmailStr
    role: Role


class RoleBody(BaseModel):
    role: Role


def _admin():
    import pathfinder.app as app_module
    return app_module.cognito_admin()


def _guard_privilege_removal(cognito, username: str, me: Principal,
                             what: str) -> None:
    """관리자를 잃는 방향의 조작을 막는다.

    두 가지를 막는다:
      - 자기 자신 — 강등·비활성·삭제 어느 쪽이든 스스로를 잠가낸다.
      - 유일한 admin — 그 계정이 사라지면 아무도 사용자 관리를 할 수 없다.

    활성화(권한을 넓히는 방향)에는 적용하지 않는다.
    """
    if username == me.username:
        raise HTTPException(
            status_code=400,
            detail=f"자신의 계정은 {what}할 수 없습니다. 다른 관리자에게 요청하세요.")
    try:
        groups = cognito.groups_of(username)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    if "admin" not in groups:
        return
    try:
        remaining = cognito.admin_count()
    except CognitoError as exc:
        raise _http_error(exc) from exc
    if remaining <= 1:
        raise HTTPException(
            status_code=400,
            detail=f"마지막 관리자는 {what}할 수 없습니다. 먼저 다른 관리자를 지정하세요.")


@router.get("/users")
async def list_users(me: Principal = Depends(require_admin)):
    cognito = _admin()
    try:
        users = cognito.list_users()
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return {"users": [
        {"username": u.username, "email": u.email, "role": u.role,
         "status": u.status, "enabled": u.enabled, "created_at": u.created_at}
        for u in users]}


@router.post("/users", status_code=201)
async def invite_user(body: InviteBody, me: Principal = Depends(require_admin)):
    """초대: 생성 → 임시 비밀번호 → 그룹.

    임시 비밀번호는 응답에 딱 한 번 실리고 어디에도 저장되지 않는다. 관리자가
    사내 메신저로 전달하고, 사용자는 첫 로그인에서 Hosted UI가 변경을 요구한다.
    """
    cognito = _admin()
    email = str(body.email)
    try:
        username = cognito.create_user(email)
    except CognitoError as exc:
        raise _http_error(exc) from exc

    password = generate_temp_password()
    # 여기부터는 사용자가 이미 존재한다 — 실패하면 방금 만든 것을 되돌린다.
    try:
        cognito.set_temp_password(username, password)
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        _log.exception("invite failed after user creation; rolling back %s", username)
        try:
            cognito.delete_user(username)
        except CognitoError:
            # 롤백까지 실패하면 역할 없는 계정이 남는다. 목록에서 role=null로
            # 보이므로 관리자가 알아볼 수 있다.
            _log.exception("rollback failed; %s may be left without a role", username)
        raise HTTPException(
            status_code=500,
            detail="사용자 생성에 실패했습니다. 다시 시도해 주세요.") from exc

    return {"username": username, "email": email, "role": body.role,
            "temp_password": password}


@router.post("/users/{username}/reset-password")
async def reset_password(username: str, me: Principal = Depends(require_admin)):
    """새 임시 비밀번호를 심고 1회 반환한다.

    이 앱은 메일을 보내지 않으므로 자가 재설정 경로가 없다 — 재설정은 관리자가
    한다(풀의 accountRecovery는 admin_only).
    """
    cognito = _admin()
    password = generate_temp_password()
    try:
        cognito.set_temp_password(username, password)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return {"username": username, "temp_password": password}


@router.put("/users/{username}/role")
async def change_role(username: str, body: RoleBody,
                      me: Principal = Depends(require_admin)):
    cognito = _admin()
    # admin으로 올리는 것은 관리자를 늘리는 방향이라 언제나 안전하다.
    if body.role != "admin":
        _guard_privilege_removal(cognito, username, me, "강등")
    try:
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return {"username": username, "role": body.role}


@router.post("/users/{username}/disable", status_code=204)
async def disable_user(username: str, me: Principal = Depends(require_admin)):
    cognito = _admin()
    _guard_privilege_removal(cognito, username, me, "비활성화")
    try:
        cognito.set_enabled(username, False)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)


@router.post("/users/{username}/enable", status_code=204)
async def enable_user(username: str, me: Principal = Depends(require_admin)):
    # 활성화는 권한을 넓히는 방향 — 마지막 관리자 보호와 무관하다.
    cognito = _admin()
    try:
        cognito.set_enabled(username, True)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)


@router.delete("/users/{username}", status_code=204)
async def delete_user(username: str, me: Principal = Depends(require_admin)):
    cognito = _admin()
    _guard_privilege_removal(cognito, username, me, "삭제")
    try:
        cognito.delete_user(username)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)
```

- [ ] **Step 5: `EmailStr` 의존성을 추가한다 (검증된 필수 단계)**

pydantic의 `EmailStr`은 `email-validator` 패키지를 요구하고, **이 venv에는 없다**
(확인: `ImportError: email-validator is not installed`). `pyproject.toml`의
dependencies에서 `"pydantic>=2.6"`을 `"pydantic[email]>=2.6"`으로 바꾼다.

Task 4 Step 1에서 `PyJWT[crypto]`를 추가한 그 줄이므로, 결과는 다음이 된다:

```toml
dependencies = ["fastapi>=0.110", "pydantic[email]>=2.6", "sse-starlette>=2.0", "httpx>=0.27", "boto3>=1.43.35", "uvicorn>=0.30", "python-dotenv>=1.0", "openpyxl>=3.1", "pypdf>=4.0", "python-multipart>=0.0.9", "strands-agents>=1.48,<2", "claude-agent-sdk==0.2.126", "PyJWT[crypto]>=2.8"]
```

Run: `cd backend && .venv/bin/pip install -e ".[dev]"`

그 뒤 다음이 통과해야 한다:

```bash
cd backend && .venv/bin/python -c "
from pydantic import BaseModel, EmailStr
class M(BaseModel):
    e: EmailStr
print('OK', M(e='a@b.io'))"
```
Expected: `OK e='a@b.io'`

- [ ] **Step 6: `app.py`의 include를 활성화한다**

Task 7에서 주석 처리한 두 줄의 주석을 벗긴다:

```python
from pathfinder.routes import admin_users  # noqa: E402
app.include_router(admin_users.router, dependencies=_AUTH)
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_admin_users.py -q`
Expected: 26 passed

- [ ] **Step 8: Task 7에서 미뤄둔 커버리지 테스트가 초록이 되는지 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_route_coverage.py -q`
Expected: 4 passed (`test_admin_routes_require_admin_not_just_user` 포함)

- [ ] **Step 9: 전체 스위트**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 통과

- [ ] **Step 10: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/routes/admin_users.py backend/tests/test_routes_admin_users.py \
        backend/pathfinder/app.py backend/pyproject.toml
git commit -m "$(cat <<'EOF'
feat(admin): 사용자 관리 API 7개 — 초대·역할·재설정·비활성·삭제

신규 가입은 초대로만 가능하므로(풀이 AllowAdminCreateUserOnly) 이 라우터가
유일한 창구다. 임시 비밀번호는 응답에 1회만 실리고 저장되지 않는다.

마지막 관리자 보호: 자기 자신 또는 유일한 admin의 강등·비활성·삭제를 400으로
막는다. 없으면 관리자가 스스로를 잠가내고 복구 경로가 AWS 콘솔밖에 안 남는다.
활성화(권한을 넓히는 방향)에는 적용하지 않는다.

초대 중간 실패는 방금 만든 사용자를 지우고 500 — 역할 없는 반쪽 계정을
남기지 않는다(projects.py 매니페스트 롤백과 같은 규율).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: 프론트 auth 라이브러리 (순수 함수)

**Files:**
- Create: `frontend/lib/auth/cognitoUrls.ts`
- Create: `frontend/lib/auth/pkce.ts`
- Create: `frontend/lib/auth/claims.ts`
- Create: `frontend/lib/auth/cookies.ts`
- Create: `frontend/lib/auth/cognitoUrls.test.ts`
- Create: `frontend/lib/auth/pkce.test.ts`
- Create: `frontend/lib/auth/claims.test.ts`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `cognitoUrls.ts`:
    - `interface CognitoEnv { domain: string; clientId: string; clientSecret: string; appUrl: string }`
    - `function cognitoEnv(): CognitoEnv` — 미설정 필드는 빈 문자열
    - `function authorizeUrl(env: CognitoEnv, challenge: string, state: string): string`
    - `function tokenEndpoint(env: CognitoEnv): string`
    - `function logoutUrl(env: CognitoEnv): string`
    - `function callbackUrl(env: CognitoEnv): string`
  - `pkce.ts`:
    - `function randomUrlSafe(bytes?: number): string`
    - `async function challengeFor(verifier: string): Promise<string>`
  - `claims.ts`:
    - `interface Claims { [k: string]: unknown }`
    - `function decodeJwtPayload(token: string): Claims | null` — **서명 검증 없음**
    - `function roleFromClaims(c: Claims | null): "admin" | "pm" | null`
    - `function emailFromClaims(c: Claims | null): string | null`
    - `function isExpired(c: Claims | null, nowSeconds: number): boolean`
  - `cookies.ts`:
    - `const ACCESS_COOKIE = "pf_access"`, `ID_COOKIE = "pf_id"`, `REFRESH_COOKIE = "pf_refresh"`
    - `const VERIFIER_COOKIE = "pf_pkce"`, `STATE_COOKIE = "pf_state"`
    - `function sessionCookieOptions(maxAgeSeconds: number): object`
    - `function transientCookieOptions(): object`

**중요:** `decodeJwtPayload`는 서명을 검증하지 **않는다**. 미들웨어의 UX 게이트와
`/api/auth/me`의 표시용 값에만 쓴다. 실제 방어선은 백엔드다. 파일 상단 주석에 이를 명시한다.

- [ ] **Step 1: `cognitoUrls` 테스트를 쓴다**

`frontend/lib/auth/cognitoUrls.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  authorizeUrl, callbackUrl, logoutUrl, tokenEndpoint, type CognitoEnv,
} from "./cognitoUrls";

const ENV: CognitoEnv = {
  domain: "pathfinder-123-ap-northeast-2.auth.ap-northeast-2.amazoncognito.com",
  clientId: "client-abc",
  clientSecret: "secret-xyz",
  appUrl: "https://d123.cloudfront.net",
};

describe("authorizeUrl", () => {
  it("builds a PKCE authorization-code request", () => {
    const url = new URL(authorizeUrl(ENV, "challenge-123", "state-456"));
    expect(url.origin).toBe(`https://${ENV.domain}`);
    expect(url.pathname).toBe("/oauth2/authorize");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("client_id")).toBe("client-abc");
    expect(url.searchParams.get("code_challenge")).toBe("challenge-123");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("state")).toBe("state-456");
    expect(url.searchParams.get("scope")).toBe("openid email profile");
    expect(url.searchParams.get("redirect_uri"))
      .toBe("https://d123.cloudfront.net/api/auth/callback");
  });

  it("never requests an implicit-flow token", () => {
    // response_type=token은 토큰을 URL 프래그먼트로 흘린다.
    const url = new URL(authorizeUrl(ENV, "c", "s"));
    expect(url.searchParams.get("response_type")).not.toContain("token");
  });
});

describe("callbackUrl", () => {
  it("matches the Cognito-registered path exactly", () => {
    // Cognito는 콜백 URL의 전수 일치만 허용한다(와일드카드 불가) — 이 문자열이
    // infra/lib/auth-client-config.ts의 CALLBACK_PATH와 같아야 한다.
    expect(callbackUrl(ENV)).toBe("https://d123.cloudfront.net/api/auth/callback");
  });

  it("does not double the slash when appUrl has a trailing one", () => {
    expect(callbackUrl({ ...ENV, appUrl: "https://d123.cloudfront.net/" }))
      .toBe("https://d123.cloudfront.net/api/auth/callback");
  });
});

describe("tokenEndpoint", () => {
  it("points at the pool's oauth2/token", () => {
    expect(tokenEndpoint(ENV)).toBe(`https://${ENV.domain}/oauth2/token`);
  });
});

describe("logoutUrl", () => {
  it("returns the user to /login", () => {
    const url = new URL(logoutUrl(ENV));
    expect(url.pathname).toBe("/logout");
    expect(url.searchParams.get("client_id")).toBe("client-abc");
    expect(url.searchParams.get("logout_uri"))
      .toBe("https://d123.cloudfront.net/login");
  });
});
```

- [ ] **Step 2: `pkce` 테스트를 쓴다**

`frontend/lib/auth/pkce.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { challengeFor, randomUrlSafe } from "./pkce";

describe("randomUrlSafe", () => {
  it("produces URL-safe strings with no padding", () => {
    const v = randomUrlSafe(32);
    // base64url: +/= 가 없어야 쿼리 파라미터로 안전하다.
    expect(v).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(v.length).toBeGreaterThanOrEqual(43);
  });

  it("does not repeat", () => {
    const seen = new Set(Array.from({ length: 50 }, () => randomUrlSafe()));
    expect(seen.size).toBe(50);
  });
});

describe("challengeFor", () => {
  it("computes the S256 challenge from a known verifier", async () => {
    // RFC 7636 Appendix B의 검증 벡터 — 우리 구현이 표준과 같은 값을 내는지
    // 확인한다. 여기가 틀리면 Cognito가 invalid_grant로만 답해 원인 파악이 어렵다.
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    await expect(challengeFor(verifier))
      .resolves.toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("is deterministic", async () => {
    const v = randomUrlSafe();
    expect(await challengeFor(v)).toBe(await challengeFor(v));
  });

  it("differs for different verifiers", async () => {
    expect(await challengeFor(randomUrlSafe()))
      .not.toBe(await challengeFor(randomUrlSafe()));
  });
});
```

- [ ] **Step 3: `claims` 테스트를 쓴다**

`frontend/lib/auth/claims.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  decodeJwtPayload, emailFromClaims, isExpired, roleFromClaims,
} from "./claims";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256", kid: "k" })}.${b64(payload)}.fake-signature`;
}

describe("decodeJwtPayload", () => {
  it("reads the payload without verifying anything", () => {
    const c = decodeJwtPayload(fakeJwt({ sub: "s-1", email: "a@b.io" }));
    expect(c).toMatchObject({ sub: "s-1", email: "a@b.io" });
  });

  it("returns null for garbage instead of throwing", () => {
    // 미들웨어가 이걸 부른다 — 예외가 나면 모든 페이지가 500이 된다.
    expect(decodeJwtPayload("not-a-jwt")).toBeNull();
    expect(decodeJwtPayload("")).toBeNull();
    expect(decodeJwtPayload("a.b")).toBeNull();
    expect(decodeJwtPayload("a.!!!.c")).toBeNull();
  });

  it("handles base64url payloads that need padding", () => {
    const c = decodeJwtPayload(fakeJwt({ a: "x".repeat(5) }));
    expect(c).not.toBeNull();
  });
});

describe("roleFromClaims", () => {
  it("reads cognito:groups", () => {
    expect(roleFromClaims({ "cognito:groups": ["admin"] })).toBe("admin");
    expect(roleFromClaims({ "cognito:groups": ["pm"] })).toBe("pm");
  });

  it("prefers admin when the user is in both groups", () => {
    // 백엔드 verifier와 같은 우선순위여야 한다 — 어긋나면 화면과 권한이 불일치한다.
    expect(roleFromClaims({ "cognito:groups": ["pm", "admin"] })).toBe("admin");
  });

  it("returns null when there is no known group", () => {
    expect(roleFromClaims({ "cognito:groups": [] })).toBeNull();
    expect(roleFromClaims({ "cognito:groups": ["other"] })).toBeNull();
    expect(roleFromClaims({})).toBeNull();
    expect(roleFromClaims(null)).toBeNull();
  });

  it("ignores a non-array groups claim", () => {
    expect(roleFromClaims({ "cognito:groups": "admin" })).toBeNull();
  });
});

describe("emailFromClaims", () => {
  it("reads the id token's email", () => {
    expect(emailFromClaims({ email: "a@b.io" })).toBe("a@b.io");
  });

  it("falls back to username when email is absent", () => {
    // access 토큰에는 email이 없다 — 그 경우에도 표시할 이름은 있어야 한다.
    expect(emailFromClaims({ username: "u@b.io" })).toBe("u@b.io");
  });

  it("returns null when neither is present", () => {
    expect(emailFromClaims({})).toBeNull();
    expect(emailFromClaims(null)).toBeNull();
  });
});

describe("isExpired", () => {
  it("compares exp against the given time", () => {
    expect(isExpired({ exp: 1000 }, 999)).toBe(false);
    expect(isExpired({ exp: 1000 }, 1001)).toBe(true);
  });

  it("treats a missing or malformed exp as expired", () => {
    // fail-closed: exp를 못 읽으면 만료로 본다(리프레시를 유발할 뿐 위험하지 않다).
    expect(isExpired({}, 0)).toBe(true);
    expect(isExpired({ exp: "soon" }, 0)).toBe(true);
    expect(isExpired(null, 0)).toBe(true);
  });
});
```

- [ ] **Step 4: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/auth`
Expected: FAIL — 세 모듈 모두 `Failed to resolve import`

- [ ] **Step 5: `cognitoUrls.ts`를 구현한다**

```typescript
// frontend/lib/auth/cognitoUrls.ts
//
// Hosted UI(managed login) 엔드포인트 조립. 순수 함수로 두어 route handler 없이
// 테스트할 수 있게 한다.
//
// ⚠️ 이 파일의 env는 모두 서버사이드 전용이다 — NEXT_PUBLIC_ 접두어를 붙이면
// 클라이언트 번들에 인라인되어 client secret이 브라우저로 나간다. 이 모듈은
// route handler와 middleware에서만 import한다.

export interface CognitoEnv {
  domain: string;
  clientId: string;
  clientSecret: string;
  appUrl: string;
}

// infra/lib/auth-client-config.ts의 CALLBACK_PATH / LOGOUT_PATH와 반드시 같아야
// 한다. Cognito는 콜백 URL의 전수 일치만 허용한다(와일드카드 불가) — 여기가
// 어긋나면 로그인이 redirect_mismatch로 실패한다.
const CALLBACK_PATH = "/api/auth/callback";
const LOGOUT_PATH = "/login";
const SCOPES = "openid email profile";

export function cognitoEnv(): CognitoEnv {
  return {
    domain: process.env.COGNITO_HOSTED_UI_DOMAIN ?? "",
    clientId: process.env.COGNITO_CLIENT_ID ?? "",
    clientSecret: process.env.COGNITO_CLIENT_SECRET ?? "",
    appUrl: process.env.APP_BASE_URL ?? "http://localhost:3000",
  };
}

function origin(appUrl: string): string {
  return appUrl.replace(/\/$/, "");
}

export function callbackUrl(env: CognitoEnv): string {
  return `${origin(env.appUrl)}${CALLBACK_PATH}`;
}

export function authorizeUrl(env: CognitoEnv, challenge: string,
                             state: string): string {
  const params = new URLSearchParams({
    response_type: "code",           // implicit(토큰을 URL 프래그먼트로 흘림)은 쓰지 않는다
    client_id: env.clientId,
    redirect_uri: callbackUrl(env),
    scope: SCOPES,
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `https://${env.domain}/oauth2/authorize?${params}`;
}

export function tokenEndpoint(env: CognitoEnv): string {
  return `https://${env.domain}/oauth2/token`;
}

export function logoutUrl(env: CognitoEnv): string {
  const params = new URLSearchParams({
    client_id: env.clientId,
    logout_uri: `${origin(env.appUrl)}${LOGOUT_PATH}`,
  });
  return `https://${env.domain}/logout?${params}`;
}
```

- [ ] **Step 6: `pkce.ts`를 구현한다**

```typescript
// frontend/lib/auth/pkce.ts
//
// PKCE(RFC 7636). Web Crypto만 쓴다 — Node 20+와 Next의 edge/node 런타임
// 양쪽에서 동작해야 하므로 node:crypto를 쓰지 않는다.

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// code_verifier와 state 양쪽에 쓴다. 43자 이상이어야 한다(RFC 7636 §4.1).
export function randomUrlSafe(bytes = 32): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return base64url(buf);
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}
```

- [ ] **Step 7: `claims.ts`를 구현한다**

```typescript
// frontend/lib/auth/claims.ts
//
// ⚠️ 여기서 서명을 검증하지 않는다. 이 모듈은 UX용이다:
//   - middleware가 /admin 게이트를 걸 때 (역할 표시/차단)
//   - /api/auth/me가 화면에 보여줄 이메일·역할을 낼 때
//
// 실제 방어선은 백엔드의 JWT 검증이다. 쿠키를 위조한 사용자는 이 파일을 속여
// /admin 화면을 열 수 있지만, 그 화면이 부르는 모든 API가 403으로 막힌다.
// 이 구분을 흐리지 말 것 — 미들웨어를 보안 경계로 착각하는 것이 이 패턴의
// 전형적인 사고다.

export interface Claims {
  [k: string]: unknown;
}

export function decodeJwtPayload(token: string): Claims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const json = Buffer.from(parts[1], "base64url").toString("utf8");
    const parsed = JSON.parse(json);
    return typeof parsed === "object" && parsed !== null ? parsed : null;
  } catch {
    // 미들웨어가 이걸 부른다 — 예외가 나면 모든 페이지가 500이 된다.
    return null;
  }
}

// 백엔드 verifier(_role_from_groups)와 같은 우선순위여야 한다: 두 그룹에 모두
// 속하면 admin. 어긋나면 화면과 실제 권한이 불일치한다.
export function roleFromClaims(c: Claims | null): "admin" | "pm" | null {
  const groups = c?.["cognito:groups"];
  if (!Array.isArray(groups)) return null;
  const names = groups.map(String);
  if (names.includes("admin")) return "admin";
  if (names.includes("pm")) return "pm";
  return null;
}

export function emailFromClaims(c: Claims | null): string | null {
  // access 토큰에는 email이 없으므로 username으로 떨어진다.
  const email = c?.email ?? c?.username;
  return typeof email === "string" && email ? email : null;
}

export function isExpired(c: Claims | null, nowSeconds: number): boolean {
  const exp = c?.exp;
  // fail-closed: exp를 못 읽으면 만료로 본다(리프레시를 유발할 뿐이다).
  if (typeof exp !== "number") return true;
  return nowSeconds > exp;
}
```

- [ ] **Step 8: `cookies.ts`를 구현한다**

```typescript
// frontend/lib/auth/cookies.ts
//
// 쿠키 이름과 속성의 단일 출처. route handler 3개와 프록시, 미들웨어가 같은
// 이름을 봐야 한다.

export const ACCESS_COOKIE = "pf_access";
export const ID_COOKIE = "pf_id";
export const REFRESH_COOKIE = "pf_refresh";

// 로그인 왕복 중에만 존재하는 값 — 콜백에서 소비하고 즉시 지운다.
export const VERIFIER_COOKIE = "pf_pkce";
export const STATE_COOKIE = "pf_state";
export const NEXT_COOKIE = "pf_next";

const isProd = () => process.env.NODE_ENV === "production";

export function sessionCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,        // JS가 토큰을 읽을 수 없다 — XSS로 탈취 불가
    secure: isProd(),      // 로컬 http 개발을 막지 않기 위해 프로덕션에서만
    // strict가 아닌 이유: Hosted UI에서 돌아오는 top-level 리다이렉트에 쿠키가
    // 실려야 한다. strict면 콜백 직후 요청에서 쿠키가 빠져 로그인이 무한 루프한다.
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

export function transientCookieOptions() {
  // PKCE verifier / state / next: 로그인 왕복(최대 10분)만 살아 있으면 된다.
  return { ...sessionCookieOptions(600) };
}

export function clearedCookieOptions() {
  return { ...sessionCookieOptions(0), maxAge: 0 };
}
```

- [ ] **Step 9: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/auth`
Expected: 3개 파일 전부 통과 (약 20 tests)

> `challengeFor`의 RFC 검증 벡터가 실패하면 base64url 변환을 확인한다 —
> 패딩(`=`)이 남아 있거나 `+`/`/`가 그대로면 값이 달라진다.

- [ ] **Step 10: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/auth
git commit -m "$(cat <<'EOF'
feat(auth): 프론트 auth 순수 함수 — Hosted UI URL · PKCE · 클레임 · 쿠키

route handler 없이 테스트되도록 순수 함수로 분리한다. PKCE 챌린지는 RFC 7636
검증 벡터로 확인한다 — 여기가 틀리면 Cognito가 invalid_grant로만 답해 원인
파악이 어렵다.

claims.ts는 서명을 검증하지 않는다(UX 게이트 전용). 실제 방어선은 백엔드이며,
roleFromClaims는 백엔드 verifier와 같은 우선순위(admin 우선)를 쓴다.

쿠키는 sameSite=lax다 — strict면 Hosted UI에서 돌아오는 top-level 리다이렉트에
쿠키가 실리지 않아 로그인이 무한 루프한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: auth route handlers 4개

**Files:**
- Create: `frontend/app/api/auth/login/route.ts`
- Create: `frontend/app/api/auth/callback/route.ts`
- Create: `frontend/app/api/auth/logout/route.ts`
- Create: `frontend/app/api/auth/me/route.ts`
- Create: `frontend/lib/auth/tokenExchange.ts` — 토큰 교환 로직(route에서 분리, 테스트 대상)
- Create: `frontend/lib/auth/tokenExchange.test.ts`
- Create: `frontend/app/api/auth/callback/route.test.ts`

**Interfaces:**
- Consumes: Task 10 전부
- Produces:
  - `tokenExchange.ts`:
    - `interface TokenSet { access_token: string; id_token: string; refresh_token?: string; expires_in: number }`
    - `async function exchangeCode(env: CognitoEnv, code: string, verifier: string, fetchImpl?: typeof fetch): Promise<TokenSet>`
    - `async function refreshTokens(env: CognitoEnv, refreshToken: string, fetchImpl?: typeof fetch): Promise<TokenSet>`
    - `class TokenExchangeError extends Error`

**왜 `tokenExchange.ts`가 따로 있는가:** Next route 파일은 HTTP 메서드 export만
허용하므로 헬퍼를 그 안에 둘 수 없고(`rewriteLocation.ts`가 같은 이유로 분리돼 있다),
Task 13의 프록시가 `refreshTokens`를 재사용한다.

- [ ] **Step 1: `tokenExchange` 테스트를 쓴다**

`frontend/lib/auth/tokenExchange.test.ts`:

```typescript
import { describe, expect, it, vi } from "vitest";
import type { CognitoEnv } from "./cognitoUrls";
import { TokenExchangeError, exchangeCode, refreshTokens } from "./tokenExchange";

const ENV: CognitoEnv = {
  domain: "pool.auth.ap-northeast-2.amazoncognito.com",
  clientId: "client-abc",
  clientSecret: "secret-xyz",
  appUrl: "https://app.example.com",
};

const TOKENS = {
  access_token: "at", id_token: "it", refresh_token: "rt",
  expires_in: 3600, token_type: "Bearer",
};

function okFetch(body: unknown = TOKENS) {
  return vi.fn(async () => new Response(JSON.stringify(body), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
}

describe("exchangeCode", () => {
  it("posts the authorization code with PKCE verifier and basic auth", async () => {
    const f = okFetch();
    const tokens = await exchangeCode(ENV, "the-code", "the-verifier", f as never);
    expect(tokens.access_token).toBe("at");

    const [url, init] = f.mock.calls[0];
    expect(url).toBe(`https://${ENV.domain}/oauth2/token`);
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"])
      .toBe("application/x-www-form-urlencoded");
    // confidential 클라이언트는 client_secret_basic으로 인증한다.
    const expected = "Basic " + Buffer.from("client-abc:secret-xyz").toString("base64");
    expect(init.headers.Authorization).toBe(expected);

    const body = new URLSearchParams(init.body as string);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("the-code");
    expect(body.get("code_verifier")).toBe("the-verifier");
    expect(body.get("redirect_uri")).toBe("https://app.example.com/api/auth/callback");
    // 시크릿은 Authorization 헤더로만 간다 — 본문에 중복해 넣지 않는다.
    expect(body.get("client_secret")).toBeNull();
  });

  it("throws TokenExchangeError on a Cognito error response", async () => {
    const f = vi.fn(async () => new Response(
      JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
    await expect(exchangeCode(ENV, "c", "v", f as never))
      .rejects.toThrow(TokenExchangeError);
  });

  it("throws when the response is missing an access token", async () => {
    const f = okFetch({ id_token: "it", expires_in: 3600 });
    await expect(exchangeCode(ENV, "c", "v", f as never))
      .rejects.toThrow(TokenExchangeError);
  });

  it("throws on a non-JSON response body", async () => {
    const f = vi.fn(async () => new Response("<html>gateway</html>", { status: 200 }));
    await expect(exchangeCode(ENV, "c", "v", f as never))
      .rejects.toThrow(TokenExchangeError);
  });
});

describe("refreshTokens", () => {
  it("posts the refresh_token grant", async () => {
    const f = okFetch({ access_token: "at2", id_token: "it2", expires_in: 3600 });
    const tokens = await refreshTokens(ENV, "the-refresh", f as never);
    expect(tokens.access_token).toBe("at2");

    const body = new URLSearchParams(f.mock.calls[0][1].body as string);
    expect(body.get("grant_type")).toBe("refresh_token");
    expect(body.get("refresh_token")).toBe("the-refresh");
    // refresh 그랜트에는 redirect_uri/code_verifier가 없다.
    expect(body.get("redirect_uri")).toBeNull();
    expect(body.get("code_verifier")).toBeNull();
  });

  it("throws when the refresh token has been revoked", async () => {
    const f = vi.fn(async () => new Response(
      JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
    await expect(refreshTokens(ENV, "revoked", f as never))
      .rejects.toThrow(TokenExchangeError);
  });
});
```

- [ ] **Step 2: 실패를 확인한 뒤 `tokenExchange.ts`를 구현한다**

Run: `cd frontend && npx vitest run lib/auth/tokenExchange`
Expected: FAIL — 모듈 없음

`frontend/lib/auth/tokenExchange.ts`:

```typescript
// frontend/lib/auth/tokenExchange.ts
//
// Cognito /oauth2/token 호출. route 파일이 아니라 여기 있는 이유:
//   - Next route 파일은 HTTP 메서드 export만 허용한다(lib/api/rewriteLocation.ts가
//     같은 이유로 분리돼 있다)
//   - /api 프록시가 refreshTokens를 재사용한다
//
// 이 코드는 서버사이드에서만 돈다 — client secret이 여기 있기 때문이다.
import { callbackUrl, tokenEndpoint, type CognitoEnv } from "./cognitoUrls";

export interface TokenSet {
  access_token: string;
  id_token: string;
  refresh_token?: string;
  expires_in: number;
}

export class TokenExchangeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TokenExchangeError";
  }
}

async function post(env: CognitoEnv, body: URLSearchParams,
                    fetchImpl: typeof fetch): Promise<TokenSet> {
  // confidential 클라이언트는 client_secret_basic으로 인증한다 — 시크릿을
  // 본문에 중복해 넣지 않는다.
  const basic = Buffer.from(`${env.clientId}:${env.clientSecret}`).toString("base64");
  const res = await fetchImpl(tokenEndpoint(env), {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: `Basic ${basic}`,
    },
    body: body.toString(),
    cache: "no-store",
  });

  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    throw new TokenExchangeError(
      `token endpoint returned a non-JSON body (status ${res.status})`);
  }
  if (!res.ok) {
    const error = (payload as { error?: string })?.error ?? "unknown_error";
    throw new TokenExchangeError(`token endpoint rejected the request: ${error}`);
  }
  const tokens = payload as Partial<TokenSet>;
  if (!tokens.access_token || !tokens.id_token) {
    throw new TokenExchangeError("token response is missing access/id token");
  }
  return {
    access_token: tokens.access_token,
    id_token: tokens.id_token,
    refresh_token: tokens.refresh_token,
    expires_in: tokens.expires_in ?? 3600,
  };
}

export async function exchangeCode(env: CognitoEnv, code: string,
                                   verifier: string,
                                   fetchImpl: typeof fetch = fetch): Promise<TokenSet> {
  return post(env, new URLSearchParams({
    grant_type: "authorization_code",
    client_id: env.clientId,
    code,
    code_verifier: verifier,
    // Cognito는 교환 시에도 redirect_uri가 authorize 때와 같은지 확인한다.
    redirect_uri: callbackUrl(env),
  }), fetchImpl);
}

export async function refreshTokens(env: CognitoEnv, refreshToken: string,
                                    fetchImpl: typeof fetch = fetch): Promise<TokenSet> {
  // refresh 그랜트는 새 refresh_token을 반환하지 않는다(기존 것이 계속 유효).
  return post(env, new URLSearchParams({
    grant_type: "refresh_token",
    client_id: env.clientId,
    refresh_token: refreshToken,
  }), fetchImpl);
}
```

Run: `cd frontend && npx vitest run lib/auth/tokenExchange`
Expected: 6 passed

- [ ] **Step 3: callback handler 테스트를 쓴다**

`frontend/app/api/auth/callback/route.test.ts`:

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest";

// route handler는 process.env를 모듈 로드 시점이 아니라 호출 시점에 읽어야
// 한다(cognitoEnv()가 함수인 이유). 테스트가 env를 먼저 세팅한다.
beforeEach(() => {
  process.env.COGNITO_HOSTED_UI_DOMAIN = "pool.auth.ap-northeast-2.amazoncognito.com";
  process.env.COGNITO_CLIENT_ID = "client-abc";
  process.env.COGNITO_CLIENT_SECRET = "secret-xyz";
  process.env.APP_BASE_URL = "https://app.example.com";
  vi.restoreAllMocks();
});

function request(url: string, cookies: Record<string, string> = {}) {
  const cookie = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`).join("; ");
  return new Request(url, { headers: cookie ? { cookie } : {} });
}

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

function mockTokenEndpoint() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({
      access_token: fakeJwt({ "cognito:groups": ["admin"], username: "a@b.io" }),
      id_token: fakeJwt({ email: "a@b.io" }),
      refresh_token: "rt",
      expires_in: 3600,
    }), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

describe("GET /api/auth/callback", () => {
  it("sets three httpOnly cookies and redirects to the saved next path", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "verifier-1", pf_state: "s1", pf_next: "/projects/p1/dashboard" },
    ) as never);

    expect(res.status).toBe(302);
    expect(res.headers.get("location"))
      .toBe("https://app.example.com/projects/p1/dashboard");

    const setCookies = res.headers.getSetCookie();
    const joined = setCookies.join("\n");
    for (const name of ["pf_access", "pf_id", "pf_refresh"]) {
      expect(joined).toContain(`${name}=`);
    }
    // 토큰이 JS에 노출되지 않아야 한다 — 이 설계의 핵심 성질이다.
    for (const c of setCookies.filter((c) => /^pf_(access|id|refresh)=/.test(c))) {
      expect(c).toMatch(/HttpOnly/i);
      expect(c).toMatch(/SameSite=Lax/i);
    }
    // 왕복용 쿠키는 소비 후 삭제된다.
    expect(joined).toMatch(/pf_pkce=;|pf_pkce=""/);
    expect(joined).toMatch(/pf_state=;|pf_state=""/);
  });

  it("defaults to / when no next cookie was saved", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1" },
    ) as never);
    expect(res.headers.get("location")).toBe("https://app.example.com/");
  });

  it("rejects a state mismatch without calling the token endpoint", async () => {
    // CSRF 방어: 공격자가 유도한 콜백은 우리가 심은 state 쿠키와 맞지 않는다.
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=attacker",
      { pf_pkce: "v", pf_state: "ours" },
    ) as never);
    expect(res.status).toBe(302);
    expect(res.headers.get("location"))
      .toBe("https://app.example.com/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("rejects a missing verifier cookie", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_state: "s1" },
    ) as never);
    expect(res.headers.get("location"))
      .toBe("https://app.example.com/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("surfaces a Hosted UI error without attempting an exchange", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?error=access_denied",
    ) as never);
    expect(res.headers.get("location"))
      .toBe("https://app.example.com/login?error=access_denied");
    expect(f).not.toHaveBeenCalled();
  });

  it("redirects to /login when the token exchange fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=stale&state=s1",
      { pf_pkce: "v", pf_state: "s1" },
    ) as never);
    expect(res.headers.get("location"))
      .toBe("https://app.example.com/login?error=exchange_failed");
  });

  it("refuses an off-site next path", async () => {
    // open redirect 방어: next는 우리 사이트 내부 경로여야 한다.
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1", pf_next: "https://evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("https://app.example.com/");
  });

  it("refuses a protocol-relative next path", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1", pf_next: "//evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("https://app.example.com/");
  });
});
```

- [ ] **Step 4: 실패를 확인한다**

Run: `cd frontend && npx vitest run app/api/auth/callback`
Expected: FAIL — `./route` 모듈 없음

- [ ] **Step 5: `login/route.ts`를 구현한다**

```typescript
// frontend/app/api/auth/login/route.ts
//
// 로그인 시작: PKCE verifier와 state를 만들어 httpOnly 쿠키에 심고 Hosted UI로
// 보낸다. verifier가 서버 쿠키에만 존재하므로 브라우저 JS는 코드 교환에
// 필요한 값을 갖지 못한다.
import { NextRequest, NextResponse } from "next/server";
import { authorizeUrl, cognitoEnv } from "@/lib/auth/cognitoUrls";
import { challengeFor, randomUrlSafe } from "@/lib/auth/pkce";
import {
  NEXT_COOKIE, STATE_COOKIE, VERIFIER_COOKIE, transientCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// 로그인 후 돌아갈 경로. 우리 사이트 내부만 허용한다(open redirect 방어).
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export async function GET(req: NextRequest) {
  const env = cognitoEnv();
  if (!env.domain || !env.clientId) {
    // 인증이 설정되지 않은 배포 — 로그인 화면이 안내 문구를 보여준다.
    return NextResponse.redirect(new URL("/login?error=not_configured", req.url));
  }
  const verifier = randomUrlSafe();
  const state = randomUrlSafe(16);
  const res = NextResponse.redirect(
    authorizeUrl(env, await challengeFor(verifier), state));
  res.cookies.set(VERIFIER_COOKIE, verifier, transientCookieOptions());
  res.cookies.set(STATE_COOKIE, state, transientCookieOptions());
  res.cookies.set(NEXT_COOKIE, safeNext(req.nextUrl.searchParams.get("next")),
                  transientCookieOptions());
  return res;
}
```

- [ ] **Step 6: `callback/route.ts`를 구현한다**

```typescript
// frontend/app/api/auth/callback/route.ts
//
// 코드 교환의 유일한 장소. 서버사이드에서 일어나므로 토큰이 브라우저 JS에
// 도달하지 않는다 — httpOnly 쿠키에만 담긴다.
import { NextRequest, NextResponse } from "next/server";
import { cognitoEnv } from "@/lib/auth/cognitoUrls";
import { exchangeCode } from "@/lib/auth/tokenExchange";
import {
  ACCESS_COOKIE, ID_COOKIE, NEXT_COOKIE, REFRESH_COOKIE, STATE_COOKIE,
  VERIFIER_COOKIE, clearedCookieOptions, sessionCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const REFRESH_MAX_AGE = 30 * 24 * 60 * 60; // 풀 클라이언트의 refresh 유효기간과 일치

// NextResponse.redirect는 절대 URL만 받는다(상대 경로는 "URL is malformed"로
// 던진다 — 확인됨). req.url을 기준으로 조립하면 프록시 뒤에서도 현재 호스트를
// 그대로 쓴다.
function toLogin(req: NextRequest, reason: string): NextResponse {
  const url = new URL("/login", req.url);
  url.searchParams.set("error", reason);
  return NextResponse.redirect(url, 302);
}

function safeNext(raw: string | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export async function GET(req: NextRequest) {
  const params = req.nextUrl.searchParams;

  // Hosted UI가 사용자 취소·설정 오류를 error로 알려준다.
  const hostedUiError = params.get("error");
  if (hostedUiError) return toLogin(req, hostedUiError);

  const code = params.get("code");
  const state = params.get("state");
  const expectedState = req.cookies.get(STATE_COOKIE)?.value;
  const verifier = req.cookies.get(VERIFIER_COOKIE)?.value;

  // CSRF 방어: 공격자가 유도한 콜백은 우리가 심은 state와 맞지 않는다.
  // verifier가 없으면(쿠키 만료·다른 브라우저) 교환 자체가 불가능하다.
  if (!code || !state || !expectedState || state !== expectedState || !verifier) {
    return toLogin(req, "state_mismatch");
  }

  let tokens;
  try {
    tokens = await exchangeCode(cognitoEnv(), code, verifier);
  } catch {
    // 사유는 서버 로그에만 — 사용자에게는 일반화된 오류를 보여준다.
    console.error("authorization code exchange failed");
    return toLogin(req, "exchange_failed");
  }

  const next = safeNext(req.cookies.get(NEXT_COOKIE)?.value);
  const res = NextResponse.redirect(new URL(next, req.url), 302);

  // access/id는 토큰 자체의 수명(expires_in), refresh는 30일.
  const session = sessionCookieOptions(tokens.expires_in);
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, session);
  res.cookies.set(ID_COOKIE, tokens.id_token, session);
  if (tokens.refresh_token) {
    res.cookies.set(REFRESH_COOKIE, tokens.refresh_token,
                    sessionCookieOptions(REFRESH_MAX_AGE));
  }
  // 왕복용 값은 소비했으므로 지운다 — 재사용(replay)을 막는다.
  for (const name of [VERIFIER_COOKIE, STATE_COOKIE, NEXT_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}
```

- [ ] **Step 7: `logout/route.ts`와 `me/route.ts`를 구현한다**

`frontend/app/api/auth/logout/route.ts`:

```typescript
// frontend/app/api/auth/logout/route.ts
//
// 쿠키를 지우고 Cognito 세션도 끊는다. 쿠키만 지우면 Hosted UI에 남은 세션
// 때문에 다음 로그인이 비밀번호를 묻지 않고 곧바로 통과한다(공용 PC 문제).
import { NextRequest, NextResponse } from "next/server";
import { cognitoEnv, logoutUrl } from "@/lib/auth/cognitoUrls";
import {
  ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE, clearedCookieOptions,
} from "@/lib/auth/cookies";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function clearAll(res: NextResponse): NextResponse {
  for (const name of [ACCESS_COOKIE, ID_COOKIE, REFRESH_COOKIE]) {
    res.cookies.set(name, "", clearedCookieOptions());
  }
  return res;
}

export async function GET(req: NextRequest) {
  const env = cognitoEnv();
  if (!env.domain || !env.clientId) {
    return clearAll(NextResponse.redirect(new URL("/login", req.url)));
  }
  return clearAll(NextResponse.redirect(logoutUrl(env)));
}

// 헤더의 로그아웃 버튼이 POST로 부를 수 있게 한다(GET 로그아웃은 프리페치에
// 걸려 의도치 않게 세션을 끊을 수 있다).
export async function POST(req: NextRequest) {
  return GET(req);
}
```

`frontend/app/api/auth/me/route.ts`:

```typescript
// frontend/app/api/auth/me/route.ts
//
// 화면에 보여줄 사용자 정보. 클라이언트가 토큰을 읽을 수 없으므로(httpOnly)
// 이 경로가 유일한 창구다.
//
// email은 id 토큰에서 읽는다 — Cognito access 토큰에는 email 클레임이 없다.
// role은 access 토큰의 cognito:groups에서 읽는다.
//
// ⚠️ 여기서 서명을 검증하지 않는다. 표시용 값이며, 실제 권한은 백엔드가 판단한다.
import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, ID_COOKIE } from "@/lib/auth/cookies";
import { decodeJwtPayload, emailFromClaims, roleFromClaims } from "@/lib/auth/claims";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const access = req.cookies.get(ACCESS_COOKIE)?.value;
  if (!access) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  const accessClaims = decodeJwtPayload(access);
  const idClaims = decodeJwtPayload(req.cookies.get(ID_COOKIE)?.value ?? "");
  return NextResponse.json({
    authenticated: true,
    email: emailFromClaims(idClaims) ?? emailFromClaims(accessClaims),
    role: roleFromClaims(accessClaims),
  });
}
```

- [ ] **Step 8: 통과를 확인한다**

Run: `cd frontend && npx vitest run app/api/auth lib/auth`
Expected: callback 8 tests + tokenExchange 6 tests + Task 10의 테스트 전부 통과



- [ ] **Step 9: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/app/api/auth frontend/lib/auth/tokenExchange.ts \
        frontend/lib/auth/tokenExchange.test.ts
git commit -m "$(cat <<'EOF'
feat(auth): login/callback/logout/me route handler

코드 교환을 서버사이드에서만 한다 — 토큰이 브라우저 JS에 도달하지 않고
httpOnly 쿠키에만 담긴다. PKCE verifier도 서버 쿠키에만 존재한다.

state 불일치·verifier 부재는 토큰 엔드포인트를 부르지 않고 거부한다(CSRF).
next 경로는 내부 경로만 허용한다(open redirect 방어 — //evil.example 포함).

로그아웃은 Cognito 세션까지 끊는다: 쿠키만 지우면 Hosted UI 세션 때문에 다음
로그인이 비밀번호를 묻지 않고 통과한다(공용 PC 문제).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: 미들웨어 — 미인증 리다이렉트 + `/admin` UX 게이트

**Files:**
- Create: `frontend/lib/auth/gate.ts` — 판정 로직(순수 함수)
- Create: `frontend/lib/auth/gate.test.ts`
- Create: `frontend/middleware.ts`

**Interfaces:**
- Consumes: Task 10의 `claims.ts`
- Produces:
  - `type GateDecision = { kind: "allow" } | { kind: "login"; next: string } | { kind: "home" }`
  - `function gateDecision(pathname: string, accessToken: string | undefined): GateDecision`

**왜 판정을 `gate.ts`로 빼는가:** `middleware.ts`는 Next 런타임(요청 객체)이 있어야
돌지만, "어느 경로가 공개인가 / 어떤 쿠키면 통과인가"는 순수 함수다. 분리하면 이
프로젝트에서 가장 실수하기 쉬운 부분(공개 경로 목록)을 직접 단정할 수 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/lib/auth/gate.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { gateDecision } from "./gate";

function jwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

const ADMIN = jwt({ "cognito:groups": ["admin"] });
const PM = jwt({ "cognito:groups": ["pm"] });

describe("public paths need no cookie", () => {
  // 설문 응답자와 프로토타입 평가자는 계정이 없다. 이 목록이 백엔드의
  // PUBLIC_PATHS와 대응해야 한다.
  it.each([
    "/login",
    "/survey/abc123",
    "/proto/p1/demo/",
    "/proto/p1/demo/styles.css",
    "/api/auth/login",
    "/api/auth/callback",
    "/api/auth/logout",
  ])("allows %s", (path) => {
    expect(gateDecision(path, undefined)).toEqual({ kind: "allow" });
  });
});

describe("protected paths without a cookie", () => {
  it("sends the user to /login with the intended path preserved", () => {
    expect(gateDecision("/projects/p1/dashboard", undefined))
      .toEqual({ kind: "login", next: "/projects/p1/dashboard" });
  });

  it("guards the project list at the root", () => {
    expect(gateDecision("/", undefined)).toEqual({ kind: "login", next: "/" });
  });

  it("guards the admin page", () => {
    expect(gateDecision("/admin/users", undefined))
      .toEqual({ kind: "login", next: "/admin/users" });
  });
});

describe("with a cookie", () => {
  it("lets any role through to project pages", () => {
    expect(gateDecision("/projects/p1/dashboard", PM)).toEqual({ kind: "allow" });
    expect(gateDecision("/projects/p1/dashboard", ADMIN)).toEqual({ kind: "allow" });
  });

  it("lets admin into /admin", () => {
    expect(gateDecision("/admin/users", ADMIN)).toEqual({ kind: "allow" });
  });

  it("bounces pm away from /admin", () => {
    // UX 게이트다 — pm에게 열리지 않을 화면을 보여주지 않는다. 실제 방어선은
    // 백엔드의 require_admin(403)이다.
    expect(gateDecision("/admin/users", PM)).toEqual({ kind: "home" });
  });

  it("bounces a roleless token away from /admin", () => {
    expect(gateDecision("/admin/users", jwt({ "cognito:groups": [] })))
      .toEqual({ kind: "home" });
  });

  it("bounces an undecodable cookie away from /admin", () => {
    // 쿠키가 깨졌으면 역할을 알 수 없다 — 관리 화면을 열지 않는다.
    expect(gateDecision("/admin/users", "garbage")).toEqual({ kind: "home" });
  });

  it("still allows non-admin pages with an undecodable cookie", () => {
    // 쿠키가 있으면 로그인 루프에 빠뜨리지 않는다 — 백엔드가 401을 주면 그때
    // 프론트가 /login으로 보낸다. 여기서 되돌리면 만료 직후 무한 왕복이 된다.
    expect(gateDecision("/projects/p1/dashboard", "garbage"))
      .toEqual({ kind: "allow" });
  });
});

describe("api proxy paths", () => {
  it("passes /api/* through — the proxy and backend judge those", () => {
    // 미들웨어가 /api를 리다이렉트하면 fetch가 HTML 로그인 페이지를 받아
    // JSON 파싱 오류로 깨진다. 401을 그대로 흘려보내는 것이 맞다.
    expect(gateDecision("/api/projects", undefined)).toEqual({ kind: "allow" });
  });
});
```

- [ ] **Step 2: 실패를 확인한 뒤 `gate.ts`를 구현한다**

Run: `cd frontend && npx vitest run lib/auth/gate`
Expected: FAIL — 모듈 없음

`frontend/lib/auth/gate.ts`:

```typescript
// frontend/lib/auth/gate.ts
//
// 미들웨어의 판정 로직. middleware.ts에서 분리한 이유는 이 목록이 이 프로젝트에서
// 가장 실수하기 쉬운 부분이라 직접 단정하고 싶기 때문이다.
//
// ⚠️ 이것은 UX 게이트다. 쿠키의 서명을 검증하지 않으므로 위조된 쿠키로 /admin
// 화면을 열 수 있지만, 그 화면이 부르는 모든 API가 백엔드에서 403으로 막힌다.
// 보안 경계는 백엔드의 require_admin이다.
import { decodeJwtPayload, roleFromClaims } from "./claims";

export type GateDecision =
  | { kind: "allow" }
  | { kind: "login"; next: string }
  | { kind: "home" };

// 로그인 없이 접근해야 하는 경로. 백엔드의
// tests/test_auth_route_coverage.py::PUBLIC_PATHS와 대응한다.
//   /login       로그인 화면 자체
//   /survey/*    익명 설문 (계정 없는 최종 사용자)
//   /proto/*     프로토타입 프리뷰 (같은 사용자가 앱을 실제로 써본다)
//   /api/auth/*  로그인 왕복 자체
const PUBLIC_PREFIXES = ["/login", "/survey/", "/proto/", "/api/auth/"];

// /api/*는 통과시킨다: 프록시가 Bearer를 붙이고 백엔드가 판단한다. 여기서
// 리다이렉트하면 fetch가 HTML 로그인 페이지를 받아 JSON 파싱 오류로 깨진다.
const PASSTHROUGH_PREFIXES = ["/api/"];

function isPublic(pathname: string): boolean {
  if (pathname === "/login") return true;
  return PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))
    || PASSTHROUGH_PREFIXES.some((p) => pathname.startsWith(p));
}

export function gateDecision(pathname: string,
                             accessToken: string | undefined): GateDecision {
  if (isPublic(pathname)) return { kind: "allow" };
  if (!accessToken) return { kind: "login", next: pathname };

  if (pathname.startsWith("/admin")) {
    // 역할을 확인할 수 없으면(쿠키 손상·그룹 없음) 관리 화면을 열지 않는다.
    if (roleFromClaims(decodeJwtPayload(accessToken)) !== "admin") {
      return { kind: "home" };
    }
  }
  // 쿠키가 있으면 통과시킨다. 만료된 쿠키를 여기서 되돌리면 백엔드의 리프레시
  // 경로를 타지 못하고 무한 왕복이 된다 — 401은 프론트가 처리한다.
  return { kind: "allow" };
}
```

- [ ] **Step 3: `middleware.ts`를 구현한다**

```typescript
// frontend/middleware.ts
//
// 로그인하지 않은 브라우저를 /login으로 보내고, pm이 관리 화면 URL을 직접 치면
// 되돌린다. 판정은 lib/auth/gate.ts가 하고 여기서는 요청/응답만 다룬다.
//
// ⚠️ 보안 경계가 아니다 — 쿠키 서명을 검증하지 않는다. 실제 권한 판단은 백엔드의
// require_user / require_admin이며, 위조 쿠키로 화면을 열어도 API가 전부 막힌다.
import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE } from "@/lib/auth/cookies";
import { gateDecision } from "@/lib/auth/gate";

export function middleware(req: NextRequest) {
  const decision = gateDecision(req.nextUrl.pathname,
                                req.cookies.get(ACCESS_COOKIE)?.value);
  if (decision.kind === "allow") return NextResponse.next();
  if (decision.kind === "home") {
    return NextResponse.redirect(new URL("/", req.url));
  }
  const login = new URL("/login", req.url);
  login.searchParams.set("next", decision.next);
  return NextResponse.redirect(login);
}

export const config = {
  // 정적 자산과 파비콘은 판정 대상이 아니다(매 요청 미들웨어 실행은 낭비다).
  // 공개 경로 판정은 gate.ts가 하므로 여기서 제외하지 않는다 — 목록이 두 곳에
  // 흩어지면 어긋난다.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/auth/gate`
Expected: 약 17 tests passed

- [ ] **Step 5: 빌드가 통과하는지 확인한다**

미들웨어는 빌드 시점에 검증되므로 여기서 한 번 확인한다.

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 6: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/middleware.ts frontend/lib/auth/gate.ts frontend/lib/auth/gate.test.ts
git commit -m "$(cat <<'EOF'
feat(auth): 미들웨어 — 미인증 리다이렉트 + /admin UX 게이트

판정을 gate.ts로 분리해 공개 경로 목록을 직접 단정한다(이 프로젝트에서 가장
실수하기 쉬운 부분). 백엔드 PUBLIC_PATHS와 대응한다.

/api/*는 통과시킨다: 미들웨어가 리다이렉트하면 fetch가 HTML 로그인 페이지를
받아 JSON 파싱 오류로 깨진다. 401은 프론트가 처리한다.

만료·손상된 쿠키도 비관리 페이지는 통과시킨다 — 여기서 되돌리면 리프레시 경로를
타지 못하고 무한 왕복이 된다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: `/api` 프록시가 Bearer를 주입하고 401을 리프레시한다

**Files:**
- Create: `frontend/lib/api/proxyAuth.ts` — 헤더 조립 + 리프레시 판정(순수 함수)
- Create: `frontend/lib/api/proxyAuth.test.ts`
- Modify: `frontend/app/api/[...path]/route.ts`

**Interfaces:**
- Consumes: Task 10의 `cookies.ts`, Task 11의 `refreshTokens`
- Produces:
  - `function withBearer(headers: Headers, accessToken: string | undefined): Headers`
  - `function isRetryableWithRefresh(status: number, method: string, hasRefresh: boolean): boolean`

**핵심:** 기존 프록시의 hop-by-hop 헤더 처리와 SSE 재스트리밍은 **그대로 유지**한다.
추가되는 것은 (a) `Cookie` 헤더 제거 + `Authorization` 주입, (b) 401 시 리프레시 1회.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/lib/api/proxyAuth.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { isRetryableWithRefresh, withBearer } from "./proxyAuth";

describe("withBearer", () => {
  it("adds the bearer header from the cookie value", () => {
    const out = withBearer(new Headers({ accept: "application/json" }), "tok-1");
    expect(out.get("authorization")).toBe("Bearer tok-1");
    expect(out.get("accept")).toBe("application/json");
  });

  it("strips the Cookie header so session cookies never reach the backend", () => {
    // 백엔드는 쿠키를 모른다. 흘려보내면 세션 토큰이 불필요하게 한 계층 더
    // 노출되고, 로그에 남을 수도 있다.
    const out = withBearer(
      new Headers({ cookie: "pf_access=secret; pf_refresh=alsosecret" }), "tok-1");
    expect(out.get("cookie")).toBeNull();
  });

  it("sends no authorization header when there is no cookie", () => {
    // 인증이 꺼진 로컬 백엔드는 헤더 없이도 응답한다.
    const out = withBearer(new Headers(), undefined);
    expect(out.get("authorization")).toBeNull();
  });

  it("replaces a client-supplied authorization header", () => {
    // 클라이언트가 보낸 Authorization을 신뢰하지 않는다 — 쿠키가 진실이다.
    const out = withBearer(new Headers({ authorization: "Bearer forged" }), "tok-1");
    expect(out.get("authorization")).toBe("Bearer tok-1");
  });

  it("drops a client-supplied authorization header when there is no cookie", () => {
    const out = withBearer(new Headers({ authorization: "Bearer forged" }), undefined);
    expect(out.get("authorization")).toBeNull();
  });
});

describe("isRetryableWithRefresh", () => {
  it("retries a GET on 401 when a refresh token exists", () => {
    expect(isRetryableWithRefresh(401, "GET", true)).toBe(true);
  });

  it("does not retry without a refresh token", () => {
    expect(isRetryableWithRefresh(401, "GET", false)).toBe(false);
  });

  it("does not retry non-401 responses", () => {
    for (const status of [200, 403, 404, 500, 502]) {
      expect(isRetryableWithRefresh(status, "GET", true)).toBe(false);
    }
  });

  it("does not retry methods that carry a streamed body", () => {
    // 요청 본문 스트림은 한 번 소비되면 되돌릴 수 없다 — 재시도하면 빈 본문이
    // 전송된다. GET/HEAD/DELETE만 안전하다.
    expect(isRetryableWithRefresh(401, "POST", true)).toBe(false);
    expect(isRetryableWithRefresh(401, "PUT", true)).toBe(false);
    expect(isRetryableWithRefresh(401, "GET", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "HEAD", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "DELETE", true)).toBe(true);
  });

  it("is case-insensitive about the method", () => {
    expect(isRetryableWithRefresh(401, "get", true)).toBe(true);
    expect(isRetryableWithRefresh(401, "post", true)).toBe(false);
  });
});
```

- [ ] **Step 2: 실패를 확인한 뒤 `proxyAuth.ts`를 구현한다**

Run: `cd frontend && npx vitest run lib/api/proxyAuth`
Expected: FAIL — 모듈 없음

`frontend/lib/api/proxyAuth.ts`:

```typescript
// frontend/lib/api/proxyAuth.ts
//
// /api 프록시의 인증 부분. route 파일이 헬퍼를 export할 수 없어 분리한다
// (rewriteLocation.ts와 같은 이유).

// 요청 본문을 재생할 수 없는 메서드 — 401 리프레시 후 재시도가 불가능하다.
// (init.body가 스트림이면 한 번 소비된 뒤 되돌릴 수 없다.)
const REPLAYABLE_METHODS = new Set(["GET", "HEAD", "DELETE"]);

export function withBearer(headers: Headers,
                           accessToken: string | undefined): Headers {
  const out = new Headers(headers);
  // 백엔드는 쿠키를 모른다. 흘려보내면 세션 토큰이 한 계층 더 노출된다.
  out.delete("cookie");
  // 클라이언트가 보낸 Authorization은 신뢰하지 않는다 — httpOnly 쿠키가 진실이다.
  out.delete("authorization");
  if (accessToken) out.set("authorization", `Bearer ${accessToken}`);
  return out;
}

export function isRetryableWithRefresh(status: number, method: string,
                                       hasRefresh: boolean): boolean {
  return status === 401 && hasRefresh
    && REPLAYABLE_METHODS.has(method.toUpperCase());
}
```

- [ ] **Step 3: 프록시 route를 수정한다**

`frontend/app/api/[...path]/route.ts`에서 다음을 바꾼다.

**(a) import를 추가한다** (파일 상단 `import { rewriteLocation } ...` 뒤):

```typescript
import { cookies } from "next/headers";
import { ACCESS_COOKIE, REFRESH_COOKIE, sessionCookieOptions } from "@/lib/auth/cookies";
import { ID_COOKIE } from "@/lib/auth/cookies";
import { isRetryableWithRefresh, withBearer } from "@/lib/api/proxyAuth";
import { cognitoEnv } from "@/lib/auth/cognitoUrls";
import { refreshTokens } from "@/lib/auth/tokenExchange";
```

**(b) `HOP_BY_HOP` 세트에 `cookie`를 추가한다** — 기존:

```typescript
const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade", "content-length",
  "content-encoding", "host",
]);
```

바꾼 뒤 (주석 포함):

```typescript
const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade", "content-length",
  "content-encoding", "host",
  // 세션 쿠키는 이 경계에서 멈춘다: withBearer()가 Authorization으로 번역하고
  // 백엔드는 쿠키를 모른다.
  "cookie",
]);
```

**(c) `proxy()` 함수를 다음으로 교체한다:**

```typescript
async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  const search = req.nextUrl.search;
  // Next's catch-all `path[]` has no empty final segment, so a request for
  // ".../demo/" would be forwarded as ".../demo" — the backend then redirects
  // back to the slash form and the browser loops. Carry the trailing slash
  // over from the incoming URL. (It is load-bearing: proxied prototypes use
  // relative asset refs that resolve against the directory form.)
  const trailingSlash = req.nextUrl.pathname.endsWith("/") ? "/" : "";
  const url = `${BACKEND}/${path.map(encodeURIComponent).join("/")}${trailingSlash}${search}`;

  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;

  const send = async (token: string | undefined): Promise<Response> => {
    const init: RequestInit & { duplex?: "half" } = {
      method: req.method,
      // 쿠키를 Bearer로 번역한다. EventSource는 커스텀 헤더를 못 보내지만
      // same-origin 쿠키는 자동으로 보내므로, SSE가 이 경로를 타면 인증된다.
      headers: withBearer(filterHeaders(req.headers), token),
      redirect: "manual",
    };
    if (req.method !== "GET" && req.method !== "HEAD") {
      init.body = req.body;
      init.duplex = "half"; // required by undici when streaming a request body
    }
    return fetch(url, init);
  };

  let res = await send(access);
  let refreshedCookies: { access: string; id: string; expiresIn: number } | null = null;

  // access 토큰 만료: 리프레시 후 한 번 재시도한다. 본문이 스트림인 메서드는
  // 재생할 수 없으므로 제외한다(isRetryableWithRefresh) — 그런 요청은 401이
  // 그대로 흘러 프론트가 /login으로 보낸다.
  if (isRetryableWithRefresh(res.status, req.method, Boolean(refresh))) {
    try {
      const tokens = await refreshTokens(cognitoEnv(), refresh as string);
      refreshedCookies = {
        access: tokens.access_token, id: tokens.id_token,
        expiresIn: tokens.expires_in,
      };
      res = await send(tokens.access_token);
    } catch {
      // 리프레시 토큰이 만료·폐기됐다 — 원래의 401을 그대로 흘린다.
      console.error("token refresh failed; passing 401 through");
    }
  }

  // Re-stream the (possibly SSE) body with clean headers only. Response(body)
  // uses the platform's own framing, so no forbidden HTTP/2 headers leak.
  const headers = filterHeaders(res.headers);
  // The backend names its own origin in redirects (e.g. Starlette's absolute
  // 307 for a missing trailing slash). Passed through verbatim, that walks the
  // browser off the public host onto localhost:8000, which it cannot reach —
  // it just hangs. Re-anchor any self-referential redirect under /api.
  const location = headers.get("location");
  if (location) headers.set("location", rewriteLocation(location, BACKEND));

  const out = new Response(res.body, { status: res.status, headers });
  // 갱신된 토큰을 브라우저 쿠키에 반영한다 — 하지 않으면 매 요청이 만료된
  // 토큰으로 시작해 리프레시를 반복한다.
  if (refreshedCookies) {
    const opts = sessionCookieOptions(refreshedCookies.expiresIn);
    const attrs = `Path=${opts.path}; Max-Age=${opts.maxAge}; HttpOnly; SameSite=Lax`
      + (opts.secure ? "; Secure" : "");
    out.headers.append("set-cookie",
      `${ACCESS_COOKIE}=${refreshedCookies.access}; ${attrs}`);
    out.headers.append("set-cookie",
      `${ID_COOKIE}=${refreshedCookies.id}; ${attrs}`);
  }
  return out;
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/api/proxyAuth`
Expected: 11 passed

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 기존 프론트 테스트가 깨지지 않았는지 확인한다**

Run: `cd frontend && npx vitest run`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/api/proxyAuth.ts frontend/lib/api/proxyAuth.test.ts \
        'frontend/app/api/[...path]/route.ts'
git commit -m "$(cat <<'EOF'
feat(auth): /api 프록시가 쿠키를 Bearer로 번역하고 401을 리프레시한다

이 번역이 SSE 인증의 근거다: EventSource는 커스텀 헤더를 못 보내지만
same-origin 쿠키는 자동 전송하므로, 프록시가 헤더로 바꿔주면 스트림도 인증된다.

쿠키는 이 경계에서 멈춘다(HOP_BY_HOP에 cookie 추가) — 백엔드는 쿠키를 모른다.
클라이언트가 보낸 Authorization도 버린다(httpOnly 쿠키가 진실).

401 재시도는 GET/HEAD/DELETE만 — 본문이 스트림인 요청은 재생할 수 없어
재시도하면 빈 본문이 전송된다. 갱신된 토큰은 응답에 Set-Cookie로 실어야
매 요청이 리프레시를 반복하지 않는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: 기존 API 클라이언트를 쿠키 인증으로 전환

**Files:**
- Modify: `frontend/lib/auth.ts`
- Modify: `frontend/lib/api/client.ts`
- Modify: `frontend/lib/api/http.ts`
- Modify: `frontend/lib/api/prototypes.ts`
- Create: `frontend/lib/auth.test.ts`

**Interfaces:**
- Consumes: 없음
- Produces: `frontend/lib/auth.ts`가 `export const CREDENTIALS: RequestCredentials = "include"`

**왜 상수를 두는가:** 세 파일이 같은 값을 쓰고, 값을 리터럴로 흩뿌리면 나중에
`same-origin`으로 바꿔야 할 때 한 곳을 놓친다. 기존 `auth.ts`가 그 자리에 있었다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/lib/auth.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import * as authModule from "./auth";
import { CREDENTIALS } from "./auth";

describe("auth seam", () => {
  it("exposes credentials:include for cookie-based auth", () => {
    // 토큰은 httpOnly 쿠키에 있고 JS가 읽을 수 없다. 클라이언트가 할 일은
    // 쿠키를 요청에 실으라고 fetch에 알리는 것뿐이다.
    expect(CREDENTIALS).toBe("include");
  });

  it("no longer exposes getAuthToken", () => {
    // 이 함수가 남아 있으면 새 호출부가 undefined 토큰을 헤더에 붙이려 시도한다.
    expect("getAuthToken" in authModule).toBe(false);
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/auth.test.ts`
Expected: FAIL — `CREDENTIALS`가 없음

- [ ] **Step 3: `lib/auth.ts`를 교체한다**

파일 전체를 다음으로 바꾼다:

```typescript
// 인증 seam. 세션은 httpOnly 쿠키(pf_access)에 있고 JS는 읽을 수 없으므로,
// 클라이언트가 할 일은 fetch에 쿠키를 실으라고 알리는 것뿐이다. same-origin
// /api 프록시가 그 쿠키를 Authorization: Bearer로 번역한다(lib/api/proxyAuth.ts).
//
// 이 상수가 리터럴이 아닌 이유: 세 클라이언트 파일이 같은 값을 쓰고, 흩뿌리면
// 정책을 바꿀 때 한 곳을 놓친다.
export const CREDENTIALS: RequestCredentials = "include";
```

- [ ] **Step 4: `lib/api/client.ts`를 수정한다**

**(a)** 1행 `import { getAuthToken } from "@/lib/auth";` →
`import { CREDENTIALS } from "@/lib/auth";`

**(b)** `authHeaders()` 함수(35–38행)를 **삭제**한다:

```typescript
function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { "X-Project-Token": token } : {};
}
```

**(c)** `request()`를 수정한다 — `...authHeaders(),` 줄을 지우고 `fetch` 호출에
`credentials`를 추가한다:

```typescript
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  // Only set Content-Type when there's a body — avoids a needless CORS preflight on GETs.
  if (init?.body !== undefined && headers["Content-Type"] === undefined) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: CREDENTIALS,
  });
```

**(d)** 파일 안에서 `authHeaders`를 쓰는 다른 곳이 없는지 확인한다:

```bash
cd frontend && grep -n "authHeaders\|getAuthToken\|X-Project-Token" lib/api/client.ts
```
Expected: 출력 없음

- [ ] **Step 5: `lib/api/http.ts`를 수정한다**

파일 전체를 다음으로 바꾼다:

```typescript
// Shared fetch wrapper. Extracted so surveys.ts/prototypes.ts don't each carry
// a copy (client.ts's own request() is unexported and assumes a JSON body,
// which 204 responses don't have).
import { API_BASE_URL, ApiError } from "./client";
import { CREDENTIALS } from "@/lib/auth";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T | null> {
  const headers: Record<string, string> = {
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: CREDENTIALS,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return null;
  return (await res.json()) as T;
}
```

- [ ] **Step 6: `lib/api/prototypes.ts`를 수정한다**

`client.ts`와 같은 세 가지 수정을 한다: import 교체, `authHeaders()` 삭제,
`request()`에 `credentials` 추가.

```bash
cd frontend && grep -n "getAuthToken\|authHeaders" lib/api/prototypes.ts
```
로 위치를 확인하고 고친 뒤, 다시 grep해서 출력이 없는지 확인한다.

- [ ] **Step 7: 레포 전체에 잔재가 없는지 확인한다**

```bash
cd /home/ec2-user/project/pathfinder-sp
grep -rn "getAuthToken\|X-Project-Token" frontend --include=*.ts --include=*.tsx \
  | grep -v node_modules
```
Expected: 출력 없음

- [ ] **Step 8: 통과를 확인한다**

Run: `cd frontend && npx vitest run`
Expected: 전부 통과 (MSW 핸들러는 헤더를 검사하지 않으므로 영향 없음)

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 9: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/auth.ts frontend/lib/auth.test.ts frontend/lib/api/client.ts \
        frontend/lib/api/http.ts frontend/lib/api/prototypes.ts
git commit -m "$(cat <<'EOF'
refactor(auth): getAuthToken 제거 — 쿠키 인증으로 전환

토큰은 httpOnly 쿠키에 있어 JS가 읽을 수 없다. 클라이언트가 할 일은 fetch에
credentials:"include"를 주는 것뿐이고, /api 프록시가 Bearer로 번역한다.
X-Project-Token 헤더는 소멸한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: 관리 API 클라이언트 + `/admin/users` 화면

**Files:**
- Create: `frontend/lib/api/adminUsers.ts`
- Create: `frontend/components/admin/TempPasswordPanel.tsx`
- Create: `frontend/components/admin/TempPasswordPanel.test.tsx`
- Create: `frontend/components/admin/InviteUserModal.tsx`
- Create: `frontend/components/admin/InviteUserModal.test.tsx`
- Create: `frontend/components/admin/UserTable.tsx`
- Create: `frontend/components/admin/UserTable.test.tsx`
- Create: `frontend/app/admin/users/page.tsx`
- Create: `frontend/app/admin/users/page.test.tsx`

**Interfaces:**
- Consumes: Task 14의 `apiFetch`
- Produces:
  - `adminUsers.ts`:
    - `interface AdminUser { username: string; email: string; role: "admin"|"pm"|null; status: string; enabled: boolean; created_at: string }`
    - `interface InviteResult { username: string; email: string; role: string; temp_password: string }`
    - `async function listUsers(): Promise<AdminUser[]>`
    - `async function inviteUser(email: string, role: "admin"|"pm"): Promise<InviteResult>`
    - `async function resetPassword(username: string): Promise<{ username: string; temp_password: string }>`
    - `async function changeRole(username: string, role: "admin"|"pm"): Promise<void>`
    - `async function setUserEnabled(username: string, enabled: boolean): Promise<void>`
    - `async function deleteUser(username: string): Promise<void>`
  - `<TempPasswordPanel email={string} password={string} onClose={() => void} />`
  - `<InviteUserModal onInvited={() => void} onClose={() => void} />`
  - `<UserTable users={AdminUser[]} currentEmail={string|null} onChanged={() => void} />`

- [ ] **Step 1: API 클라이언트를 쓴다**

`frontend/lib/api/adminUsers.ts`:

```typescript
// frontend/lib/api/adminUsers.ts — /admin/users* 클라이언트.
//
// 서버는 username과 email을 둘 다 준다. 화면은 email을 보여주고 액션은 username을
// 보낸다 — 지금은 두 값이 같지만 화면이 그 등식에 의존하지 않게 한다.
import { apiFetch } from "./http";

export type UserRole = "admin" | "pm";

export interface AdminUser {
  username: string;
  email: string;
  role: UserRole | null;   // 그룹 미배정(반쪽 계정)이면 null
  status: string;          // CONFIRMED / FORCE_CHANGE_PASSWORD / ...
  enabled: boolean;
  created_at: string;
}

export interface InviteResult {
  username: string;
  email: string;
  role: string;
  temp_password: string;
}

export async function listUsers(): Promise<AdminUser[]> {
  const body = await apiFetch<{ users: AdminUser[] }>("/admin/users");
  return body?.users ?? [];
}

export async function inviteUser(email: string, role: UserRole): Promise<InviteResult> {
  const body = await apiFetch<InviteResult>("/admin/users", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
  if (!body) throw new Error("invite returned an empty body");
  return body;
}

export async function resetPassword(
  username: string,
): Promise<{ username: string; temp_password: string }> {
  const body = await apiFetch<{ username: string; temp_password: string }>(
    `/admin/users/${encodeURIComponent(username)}/reset-password`,
    { method: "POST" },
  );
  if (!body) throw new Error("reset returned an empty body");
  return body;
}

export async function changeRole(username: string, role: UserRole): Promise<void> {
  await apiFetch(`/admin/users/${encodeURIComponent(username)}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export async function setUserEnabled(username: string, enabled: boolean): Promise<void> {
  await apiFetch(
    `/admin/users/${encodeURIComponent(username)}/${enabled ? "enable" : "disable"}`,
    { method: "POST" },
  );
}

export async function deleteUser(username: string): Promise<void> {
  await apiFetch(`/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" });
}
```

- [ ] **Step 2: `TempPasswordPanel` 테스트를 쓴다**

`frontend/components/admin/TempPasswordPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TempPasswordPanel } from "./TempPasswordPanel";

describe("TempPasswordPanel", () => {
  it("shows the password and warns that it cannot be seen again", () => {
    render(<TempPasswordPanel email="new@x.io" password="Ab3!xyz789QWERty"
                              onClose={() => {}} />);
    expect(screen.getByText("Ab3!xyz789QWERty")).toBeInTheDocument();
    expect(screen.getByText("new@x.io")).toBeInTheDocument();
    // 서버가 저장하지 않으므로 이 경고가 없으면 관리자가 값을 잃는다.
    expect(screen.getByText(/다시 볼 수 없습니다/)).toBeInTheDocument();
  });

  it("copies the password to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<TempPasswordPanel email="new@x.io" password="pw-1" onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /복사/ }));
    expect(writeText).toHaveBeenCalledWith("pw-1");
    expect(await screen.findByText(/복사했습니다/)).toBeInTheDocument();
  });

  it("calls onClose when the confirm button is pressed", async () => {
    const onClose = vi.fn();
    render(<TempPasswordPanel email="new@x.io" password="pw-1" onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: /확인/ }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 3: `TempPasswordPanel`을 구현한다**

`frontend/components/admin/TempPasswordPanel.tsx`:

```tsx
"use client";
import { useState } from "react";

// 임시 비밀번호는 서버가 저장하지 않는다 — 이 화면이 그 값을 볼 수 있는 유일한
// 기회다. 그래서 경고와 복사 버튼이 함께 있다.
export function TempPasswordPanel({
  email, password, onClose,
}: {
  email: string;
  password: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
    } catch {
      // 클립보드 권한이 없는 브라우저 — 값은 화면에 보이므로 수동 복사가 가능하다.
      setCopied(false);
    }
  }

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
      <p className="text-sm font-medium text-amber-900">
        {email} 의 임시 비밀번호
      </p>
      <div className="mt-2 flex items-center gap-2">
        <code className="flex-1 rounded bg-white px-3 py-2 font-mono text-sm border border-amber-200">
          {password}
        </code>
        <button
          type="button"
          onClick={copy}
          className="rounded-lg bg-amber-600 px-3 py-2 text-sm text-white hover:bg-amber-700"
        >
          복사
        </button>
      </div>
      {copied && <p className="mt-1 text-xs text-amber-700">복사했습니다.</p>}
      <p className="mt-2 text-xs text-amber-800">
        이 창을 닫으면 <strong>다시 볼 수 없습니다</strong>. 사용자에게 전달하세요.
        사용자는 첫 로그인에서 비밀번호를 변경합니다.
      </p>
      <button
        type="button"
        onClick={onClose}
        className="mt-3 rounded-lg border border-amber-300 px-3 py-1.5 text-sm text-amber-900 hover:bg-amber-100"
      >
        확인
      </button>
    </div>
  );
}
```

- [ ] **Step 4: `InviteUserModal` 테스트와 구현을 쓴다**

`frontend/components/admin/InviteUserModal.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";
import { InviteUserModal } from "./InviteUserModal";

describe("InviteUserModal", () => {
  it("invites a user and reveals the temp password once", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, async ({ request }) => {
      const body = (await request.json()) as { email: string; role: string };
      expect(body).toEqual({ email: "new@x.io", role: "pm" });
      return HttpResponse.json({
        username: "new@x.io", email: "new@x.io", role: "pm",
        temp_password: "Tmp!2345678abcd",
      }, { status: 201 });
    }));

    const onInvited = vi.fn();
    render(<InviteUserModal onInvited={onInvited} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("이메일"), "new@x.io");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));

    expect(await screen.findByText("Tmp!2345678abcd")).toBeInTheDocument();
    // 목록 갱신은 비밀번호를 보여준 뒤에 알린다.
    expect(onInvited).toHaveBeenCalled();
  });

  it("defaults the role to pm", () => {
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    expect(screen.getByLabelText("역할")).toHaveValue("pm");
  });

  it("shows the server message when the email already exists", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, () =>
      HttpResponse.json({ detail: "이미 등록된 이메일입니다." }, { status: 409 })));
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("이메일"), "dup@x.io");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));
    expect(await screen.findByText("이미 등록된 이메일입니다.")).toBeInTheDocument();
  });

  it("does not submit an empty email", async () => {
    const handler = vi.fn();
    server.use(http.post(`${API_BASE_URL}/admin/users`, () => {
      handler();
      return HttpResponse.json({}, { status: 201 });
    }));
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "초대" }));
    expect(handler).not.toHaveBeenCalled();
  });

  it("closes without inviting", async () => {
    const onClose = vi.fn();
    render(<InviteUserModal onInvited={() => {}} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
```

`frontend/components/admin/InviteUserModal.tsx`:

```tsx
"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { inviteUser, type InviteResult, type UserRole } from "@/lib/api/adminUsers";
import { TempPasswordPanel } from "./TempPasswordPanel";

// 신규 가입은 초대로만 가능하다(풀이 self-signup을 막는다). 이 모달이 그 창구다.
export function InviteUserModal({
  onInvited, onClose,
}: {
  onInvited: () => void;
  onClose: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<UserRole>("pm");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<InviteResult | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const invited = await inviteUser(email.trim(), role);
      setResult(invited);
      // 목록은 곧바로 갱신하되 모달은 닫지 않는다 — 비밀번호를 보여줘야 한다.
      onInvited();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "초대에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-lg">
        <h2 className="text-lg font-bold">사용자 초대</h2>
        {result ? (
          <div className="mt-4">
            <TempPasswordPanel email={result.email} password={result.temp_password}
                               onClose={onClose} />
          </div>
        ) : (
          <form onSubmit={submit} className="mt-4 space-y-4">
            <div>
              <label htmlFor="invite-email" className="block text-sm font-medium">
                이메일
              </label>
              <input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                placeholder="user@example.com"
              />
            </div>
            <div>
              <label htmlFor="invite-role" className="block text-sm font-medium">
                역할
              </label>
              <select
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value as UserRole)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="pm">PM — 프로젝트 전체 접근</option>
                <option value="admin">관리자 — PM 권한 + 사용자 관리</option>
              </select>
            </div>
            {error && <p className="text-sm text-rose-600">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose}
                      className="rounded-lg border border-slate-300 px-4 py-2 text-sm">
                취소
              </button>
              <button type="submit" disabled={busy}
                      className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-50">
                {busy ? "초대 중…" : "초대"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: `UserTable` 테스트를 쓴다**

`frontend/components/admin/UserTable.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";
import type { AdminUser } from "@/lib/api/adminUsers";
import { UserTable } from "./UserTable";

const USERS: AdminUser[] = [
  { username: "admin@pathfinder.local", email: "admin@pathfinder.local",
    role: "admin", status: "CONFIRMED", enabled: true,
    created_at: "2026-07-25T00:00:00+00:00" },
  { username: "pm@pathfinder.local", email: "pm@pathfinder.local",
    role: "pm", status: "FORCE_CHANGE_PASSWORD", enabled: true,
    created_at: "2026-07-25T01:00:00+00:00" },
  { username: "off@x.io", email: "off@x.io", role: "pm",
    status: "CONFIRMED", enabled: false, created_at: "2026-07-25T02:00:00+00:00" },
];

function row(email: string) {
  return screen.getByRole("row", { name: new RegExp(email) });
}

describe("UserTable", () => {
  it("renders email, role and status for each user", () => {
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    expect(within(row("admin@pathfinder.local")).getByText("관리자")).toBeInTheDocument();
    expect(within(row("pm@pathfinder.local")).getByText("PM")).toBeInTheDocument();
    // 초대 직후 상태는 "비밀번호 변경 필요"로 읽혀야 한다.
    expect(within(row("pm@pathfinder.local")).getByText(/변경 필요/)).toBeInTheDocument();
    expect(within(row("off@x.io")).getByText("비활성")).toBeInTheDocument();
  });

  it("marks the current user so they know which row is theirs", () => {
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    expect(within(row("admin@pathfinder.local")).getByText(/나/)).toBeInTheDocument();
  });

  it("shows a role=null user as 역할 없음", () => {
    // 초대 롤백이 실패해 남은 반쪽 계정을 관리자가 알아볼 수 있어야 한다.
    render(<UserTable users={[{ ...USERS[1], role: null }]} currentEmail={null}
                      onChanged={() => {}} />);
    expect(screen.getByText("역할 없음")).toBeInTheDocument();
  });

  it("changes a role and reloads", async () => {
    const onChanged = vi.fn();
    let received: unknown = null;
    server.use(http.put(
      `${API_BASE_URL}/admin/users/pm@pathfinder.local/role`,
      async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({ username: "pm@pathfinder.local", role: "admin" });
      }));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={onChanged} />);
    await userEvent.selectOptions(
      within(row("pm@pathfinder.local")).getByLabelText(/역할 변경/), "admin");
    expect(received).toEqual({ role: "admin" });
    expect(onChanged).toHaveBeenCalled();
  });

  it("surfaces the server's refusal to demote the last admin", async () => {
    server.use(http.put(
      `${API_BASE_URL}/admin/users/admin@pathfinder.local/role`, () =>
        HttpResponse.json(
          { detail: "마지막 관리자는 강등할 수 없습니다. 먼저 다른 관리자를 지정하세요." },
          { status: 400 })));
    render(<UserTable users={USERS} currentEmail="other@x.io" onChanged={() => {}} />);
    await userEvent.selectOptions(
      within(row("admin@pathfinder.local")).getByLabelText(/역할 변경/), "pm");
    expect(await screen.findByText(/마지막 관리자는 강등할 수 없습니다/))
      .toBeInTheDocument();
  });

  it("resets a password and shows it once", async () => {
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@pathfinder.local/reset-password`, () =>
        HttpResponse.json({ username: "pm@pathfinder.local",
                            temp_password: "New!23456789abc" })));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: /비밀번호 재설정/ }));
    expect(await screen.findByText("New!23456789abc")).toBeInTheDocument();
  });

  it("disables an enabled user", async () => {
    const onChanged = vi.fn();
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@pathfinder.local/disable`, () =>
        new HttpResponse(null, { status: 204 })));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: "비활성화" }));
    expect(onChanged).toHaveBeenCalled();
  });

  it("offers 활성화 for a disabled user", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users/off@x.io/enable`, () =>
      new HttpResponse(null, { status: 204 })));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    expect(within(row("off@x.io")).getByRole("button", { name: "활성화" }))
      .toBeInTheDocument();
  });

  it("requires confirmation before deleting", async () => {
    const onChanged = vi.fn();
    const handler = vi.fn();
    server.use(http.delete(`${API_BASE_URL}/admin/users/pm@pathfinder.local`, () => {
      handler();
      return new HttpResponse(null, { status: 204 });
    }));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: "삭제" }));
    // 첫 클릭은 확인을 띄우기만 한다 — 되돌릴 수 없는 조작이다.
    expect(handler).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /삭제 확인/ }));
    expect(handler).toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("can back out of the delete confirmation", async () => {
    const handler = vi.fn();
    server.use(http.delete(`${API_BASE_URL}/admin/users/pm@pathfinder.local`, () => {
      handler();
      return new HttpResponse(null, { status: 204 });
    }));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: "삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(handler).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 6: `UserTable`을 구현한다**

`frontend/components/admin/UserTable.tsx`:

```tsx
"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  changeRole, deleteUser, resetPassword, setUserEnabled,
  type AdminUser, type UserRole,
} from "@/lib/api/adminUsers";
import { TempPasswordPanel } from "./TempPasswordPanel";

const ROLE_LABEL: Record<string, string> = { admin: "관리자", pm: "PM" };

function statusLabel(user: AdminUser): string {
  if (!user.enabled) return "비활성";
  if (user.status === "FORCE_CHANGE_PASSWORD") return "비밀번호 변경 필요";
  if (user.status === "CONFIRMED") return "정상";
  return user.status;
}

export function UserTable({
  users, currentEmail, onChanged,
}: {
  users: AdminUser[];
  currentEmail: string | null;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<AdminUser | null>(null);
  const [revealed, setRevealed] = useState<{ email: string; password: string } | null>(null);

  // 서버가 정책 위반(마지막 관리자 보호 등)을 400으로 알려주면 그 문장을 그대로
  // 보여준다 — 프론트가 규칙을 복제하면 두 곳이 어긋난다.
  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "요청이 실패했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function doReset(user: AdminUser) {
    setBusy(`reset:${user.username}`);
    setError(null);
    try {
      const { temp_password } = await resetPassword(user.username);
      setRevealed({ email: user.email, password: temp_password });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "재설정에 실패했습니다.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <p role="alert" className="rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </p>
      )}
      {revealed && (
        <TempPasswordPanel email={revealed.email} password={revealed.password}
                           onClose={() => setRevealed(null)} />
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="px-4 py-3">이메일</th>
              <th className="px-4 py-3">역할</th>
              <th className="px-4 py-3">상태</th>
              <th className="px-4 py-3">생성일</th>
              <th className="px-4 py-3">작업</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const isMe = currentEmail === user.email;
              return (
                <tr key={user.username} className="border-t border-slate-100">
                  <td className="px-4 py-3">
                    {user.email}
                    {isMe && <span className="ml-2 text-xs text-violet-600">(나)</span>}
                  </td>
                  <td className="px-4 py-3">
                    {user.role
                      ? <span className={user.role === "admin"
                          ? "rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700"
                          : "rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"}>
                          {ROLE_LABEL[user.role]}
                        </span>
                      : <span className="text-xs text-amber-700">역할 없음</span>}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{statusLabel(user)}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {user.created_at.slice(0, 10)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <label className="sr-only" htmlFor={`role-${user.username}`}>
                        {user.email} 역할 변경
                      </label>
                      <select
                        id={`role-${user.username}`}
                        value={user.role ?? ""}
                        disabled={busy !== null}
                        onChange={(e) => run(`role:${user.username}`, () =>
                          changeRole(user.username, e.target.value as UserRole))}
                        className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
                      >
                        {!user.role && <option value="">역할 선택</option>}
                        <option value="pm">PM</option>
                        <option value="admin">관리자</option>
                      </select>
                      <button type="button" disabled={busy !== null}
                              onClick={() => doReset(user)}
                              className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">
                        비밀번호 재설정
                      </button>
                      <button type="button" disabled={busy !== null}
                              onClick={() => run(`enabled:${user.username}`, () =>
                                setUserEnabled(user.username, !user.enabled))}
                              className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">
                        {user.enabled ? "비활성화" : "활성화"}
                      </button>
                      <button type="button" disabled={busy !== null}
                              onClick={() => setConfirmDelete(user)}
                              className="rounded-lg border border-rose-200 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50">
                        삭제
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {confirmDelete && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h3 className="font-bold">사용자 삭제</h3>
            <p className="mt-2 text-sm text-slate-600">
              <strong>{confirmDelete.email}</strong> 계정을 삭제합니다.
              되돌릴 수 없습니다.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setConfirmDelete(null)}
                      className="rounded-lg border border-slate-300 px-4 py-2 text-sm">
                취소
              </button>
              <button
                type="button"
                onClick={() => {
                  const target = confirmDelete;
                  setConfirmDelete(null);
                  void run(`delete:${target.username}`,
                           () => deleteUser(target.username));
                }}
                className="rounded-lg bg-rose-600 px-4 py-2 text-sm text-white hover:bg-rose-700"
              >
                삭제 확인
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 7: 페이지 테스트와 구현을 쓴다**

`frontend/app/admin/users/page.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";
import AdminUsersPage from "./page";

const USERS = [
  { username: "admin@pathfinder.local", email: "admin@pathfinder.local",
    role: "admin", status: "CONFIRMED", enabled: true,
    created_at: "2026-07-25T00:00:00+00:00" },
];

function mockList() {
  server.use(
    http.get(`${API_BASE_URL}/admin/users`, () => HttpResponse.json({ users: USERS })),
    http.get("/api/auth/me", () => HttpResponse.json({
      authenticated: true, email: "admin@pathfinder.local", role: "admin",
    })),
  );
}

describe("/admin/users", () => {
  it("lists users", async () => {
    mockList();
    render(<AdminUsersPage />);
    expect(await screen.findByText("admin@pathfinder.local")).toBeInTheDocument();
  });

  it("opens the invite modal", async () => {
    mockList();
    render(<AdminUsersPage />);
    await screen.findByText("admin@pathfinder.local");
    await userEvent.click(screen.getByRole("button", { name: "사용자 초대" }));
    expect(screen.getByLabelText("이메일")).toBeInTheDocument();
  });

  it("explains a 403 instead of showing an empty table", async () => {
    // pm이 URL을 직접 쳐서 들어온 경우 — 미들웨어를 우회했더라도 API가 막는다.
    server.use(
      http.get(`${API_BASE_URL}/admin/users`, () =>
        HttpResponse.json({ detail: "admin role required" }, { status: 403 })),
      http.get("/api/auth/me", () => HttpResponse.json({
        authenticated: true, email: "pm@pathfinder.local", role: "pm",
      })),
    );
    render(<AdminUsersPage />);
    expect(await screen.findByText(/관리자 권한이 필요합니다/)).toBeInTheDocument();
  });

  it("shows a generic error when the list fails", async () => {
    server.use(
      http.get(`${API_BASE_URL}/admin/users`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 502 })),
      http.get("/api/auth/me", () => HttpResponse.json({
        authenticated: true, email: "admin@pathfinder.local", role: "admin",
      })),
    );
    render(<AdminUsersPage />);
    expect(await screen.findByText(/사용자 목록을 불러오지 못했습니다/))
      .toBeInTheDocument();
  });
});
```

`frontend/app/admin/users/page.tsx`:

```tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { UserTable } from "@/components/admin/UserTable";
import { InviteUserModal } from "@/components/admin/InviteUserModal";
import { ApiError } from "@/lib/api/client";
import { listUsers, type AdminUser } from "@/lib/api/adminUsers";

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);
  const [me, setMe] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      setUsers(await listUsers());
    } catch (err) {
      // 403은 pm이 URL을 직접 친 경우다 — 미들웨어는 UX 게이트일 뿐이고
      // 실제 차단은 여기(백엔드 응답)에서 드러난다.
      setError(err instanceof ApiError && err.status === 403
        ? "관리자 권한이 필요합니다."
        : "사용자 목록을 불러오지 못했습니다.");
      setUsers([]);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    // 자기 행을 표시하기 위한 이메일. 실패는 무해하다(표시가 빠질 뿐).
    void fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => setMe(body?.email ?? null))
      .catch(() => setMe(null));
  }, []);

  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold">사용자 관리</h1>
            <p className="mt-1 text-sm text-slate-500">
              신규 가입은 초대로만 가능합니다. 초대하면 임시 비밀번호가 한 번
              표시되며, 사용자는 첫 로그인에서 비밀번호를 변경합니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setInviting(true)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700"
          >
            사용자 초대
          </button>
        </div>

        {error && (
          <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </p>
        )}
        {users === null && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {users !== null && users.length > 0 && (
          <UserTable users={users} currentEmail={me} onChanged={reload} />
        )}
        {users !== null && users.length === 0 && !error && (
          <p className="text-sm text-slate-500">사용자가 없습니다.</p>
        )}

        {inviting && (
          <InviteUserModal
            onInvited={reload}
            onClose={() => setInviting(false)}
          />
        )}
      </main>
    </>
  );
}
```

- [ ] **Step 8: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/admin app/admin lib/api/adminUsers`
Expected: 약 22 tests passed

> `page.test.tsx`가 `/api/auth/me` 호출에서 MSW `onUnhandledRequest: "error"`로
> 실패하면, 상대 URL을 MSW가 절대 URL로 해석하지 못하는 것이다. 그 경우 테스트의
> 핸들러를 `http.get("*/api/auth/me", ...)`로 바꾼다.

- [ ] **Step 9: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/api/adminUsers.ts frontend/components/admin frontend/app/admin
git commit -m "$(cat <<'EOF'
feat(admin): 사용자 관리 화면 — 초대·역할·재설정·비활성·삭제

임시 비밀번호는 서버가 저장하지 않으므로 TempPasswordPanel이 그 값을 볼 수 있는
유일한 기회다 — 복사 버튼과 "다시 볼 수 없습니다" 경고를 함께 둔다.

정책 위반(마지막 관리자 보호)은 서버 메시지를 그대로 보여준다. 프론트가 규칙을
복제하면 두 곳이 어긋난다.

403은 "관리자 권한이 필요합니다"로 구분해 보여준다 — pm이 URL을 직접 친 경우이며,
미들웨어가 UX 게이트일 뿐이고 실제 차단이 여기서 드러난다는 사실의 표현이다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: 로그인 화면 + 헤더 사용자 메뉴

**Files:**
- Create: `frontend/app/login/page.tsx`
- Create: `frontend/app/login/page.test.tsx`
- Create: `frontend/components/UserMenu.tsx`
- Create: `frontend/components/UserMenu.test.tsx`
- Modify: `frontend/components/AppHeader.tsx` (65–70행의 하드코딩 "김PM" 버튼)
- Modify: `frontend/components/AppHeader.test.tsx` (하드코딩 단정이 있으면)

**Interfaces:**
- Consumes: Task 11의 `/api/auth/me`, `/api/auth/login`, `/api/auth/logout`
- Produces: `<UserMenu />` — 자체적으로 `/api/auth/me`를 부른다(부모가 prop을 넘기지 않는다)

- [ ] **Step 1: 로그인 화면 테스트를 쓴다**

`frontend/app/login/page.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LoginPage from "./page";

// useSearchParams를 쓰는 화면이라 next/navigation을 목한다.
const searchParams = { value: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams.value,
}));

function withParams(query: string) {
  searchParams.value = new URLSearchParams(query);
}

describe("/login", () => {
  it("links to the Hosted UI login route", () => {
    withParams("");
    render(<LoginPage />);
    const link = screen.getByRole("link", { name: /로그인/ });
    expect(link).toHaveAttribute("href", "/api/auth/login");
  });

  it("carries the next path into the login route", () => {
    // 미들웨어가 붙여준 next를 잃지 않아야 원래 가려던 화면으로 돌아간다.
    withParams("next=%2Fprojects%2Fp1%2Fdashboard");
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: /로그인/ }))
      .toHaveAttribute("href", "/api/auth/login?next=%2Fprojects%2Fp1%2Fdashboard");
  });

  it("explains a state mismatch in Korean", () => {
    withParams("error=state_mismatch");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/다시 시도/);
  });

  it("explains a cancelled login", () => {
    withParams("error=access_denied");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/취소/);
  });

  it("explains an unconfigured deployment", () => {
    // 인증 env 없이 배포된 경우 — 무엇을 고쳐야 하는지 알려준다.
    withParams("error=not_configured");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/설정되지 않았습니다/);
  });

  it("falls back to a generic message for an unknown error code", () => {
    withParams("error=weird_thing");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/로그인에 실패했습니다/);
  });

  it("shows no alert when there is no error", () => {
    withParams("");
    render(<LoginPage />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not reflect the raw error code into the page", () => {
    // 쿼리 파라미터를 그대로 렌더하면 반사형 XSS 표면이 된다.
    withParams("error=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E");
    render(<LoginPage />);
    expect(document.body.innerHTML).not.toContain("onerror");
  });
});
```

- [ ] **Step 2: 로그인 화면을 구현한다**

`frontend/app/login/page.tsx`:

```tsx
"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

// 오류 코드 → 한국어 안내. 알 수 없는 코드는 일반 문구로 떨어진다 — 쿼리
// 파라미터를 그대로 렌더하면 반사형 XSS 표면이 된다.
const MESSAGES: Record<string, string> = {
  state_mismatch: "로그인 요청이 만료되었거나 일치하지 않습니다. 다시 시도해 주세요.",
  exchange_failed: "인증 서버와의 통신에 실패했습니다. 다시 시도해 주세요.",
  access_denied: "로그인이 취소되었습니다.",
  not_configured: "인증이 설정되지 않았습니다. 관리자에게 문의하세요.",
};

export default function LoginPage() {
  const params = useSearchParams();
  const error = params.get("error");
  const next = params.get("next");
  const href = next
    ? `/api/auth/login?next=${encodeURIComponent(next)}`
    : "/api/auth/login";

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-sm">
        <div className="flex items-center gap-2 text-lg font-bold text-violet-700">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-600 text-sm font-bold text-white">
            AI
          </span>
          Pathfinder
        </div>
        <h1 className="mt-6 text-xl font-bold">로그인</h1>
        <p className="mt-1 text-sm text-slate-500">
          워크숍 계정으로 로그인하세요. 계정이 없으면 관리자에게 초대를 요청하세요.
        </p>

        {error && (
          <p role="alert" className="mt-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {MESSAGES[error] ?? "로그인에 실패했습니다. 다시 시도해 주세요."}
          </p>
        )}

        {/* Link가 아니라 a여야 한다: /api/auth/login은 페이지가 아니라 외부
            리다이렉트를 내는 route handler이고, 클라이언트 라우팅으로는 그
            리다이렉트를 따라갈 수 없다. */}
        <a
          href={href}
          className="mt-6 block w-full rounded-lg bg-violet-600 px-4 py-3 text-center text-sm font-medium text-white hover:bg-violet-700"
        >
          로그인
        </a>
      </div>
    </main>
  );
}
```

> `Link`를 import했지만 쓰지 않으면 lint가 경고한다 — 위 코드에서 `import Link`
> 줄을 지운다. (의도적으로 `<a>`를 쓴다: route handler의 외부 리다이렉트는
> 클라이언트 라우팅으로 따라갈 수 없다.)

- [ ] **Step 3: `UserMenu` 테스트를 쓴다**

`frontend/components/UserMenu.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/test/msw/server";
import { UserMenu } from "./UserMenu";

function mockMe(body: unknown, status = 200) {
  server.use(http.get("*/api/auth/me", () => HttpResponse.json(body, { status })));
}

describe("UserMenu", () => {
  it("shows the signed-in email's initial", async () => {
    mockMe({ authenticated: true, email: "admin@pathfinder.local", role: "admin" });
    render(<UserMenu />);
    expect(await screen.findByRole("button", { name: /사용자 메뉴/ }))
      .toHaveTextContent("A");
  });

  it("reveals email, role and logout when opened", async () => {
    mockMe({ authenticated: true, email: "pm@pathfinder.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByText("pm@pathfinder.local")).toBeInTheDocument();
    expect(screen.getByText("PM")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();
  });

  it("offers 사용자 관리 to an admin", async () => {
    mockMe({ authenticated: true, email: "admin@pathfinder.local", role: "admin" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByRole("link", { name: "사용자 관리" }))
      .toHaveAttribute("href", "/admin/users");
  });

  it("hides 사용자 관리 from a pm", async () => {
    // pm에게 열리지 않을 화면의 링크를 보여주지 않는다(실제 차단은 백엔드).
    mockMe({ authenticated: true, email: "pm@pathfinder.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.queryByRole("link", { name: "사용자 관리" })).toBeNull();
  });

  it("renders nothing when not authenticated", async () => {
    // 로그인 화면 등 인증 전 화면에서 빈 아바타가 뜨지 않게 한다.
    mockMe({ authenticated: false }, 401);
    const { container } = render(<UserMenu />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector("button")).toBeNull();
  });

  it("logs out via POST so a prefetch cannot end the session", async () => {
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    const form = screen.getByRole("button", { name: "로그아웃" }).closest("form");
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", "/api/auth/logout");
  });
});
```

- [ ] **Step 4: `UserMenu`를 구현한다**

`frontend/components/UserMenu.tsx`:

```tsx
"use client";
import Link from "next/link";
import { useEffect, useState } from "react";

interface Me {
  authenticated: boolean;
  email?: string | null;
  role?: "admin" | "pm" | null;
}

const ROLE_LABEL: Record<string, string> = { admin: "관리자", pm: "PM" };

// 토큰이 httpOnly 쿠키에 있어 JS가 읽을 수 없으므로, 표시용 정보는 서버에게
// 묻는다(/api/auth/me). 부모가 prop으로 넘기지 않는 이유: 이 컴포넌트가 헤더의
// 모든 화면에 들어가므로 각 페이지가 사용자 정보를 실어 보내는 배선을 만들지 않는다.
export function UserMenu() {
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { authenticated: false }))
      .then((body) => { if (alive) setMe(body); })
      .catch(() => { if (alive) setMe({ authenticated: false }); });
    return () => { alive = false; };
  }, []);

  // 인증 전(로그인 화면)에는 아무것도 그리지 않는다.
  if (!me?.authenticated || !me.email) return null;

  const initial = me.email.charAt(0).toUpperCase();

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`사용자 메뉴 (${me.email})`}
        aria-expanded={open}
        className="h-9 w-9 rounded-full bg-violet-100 text-sm font-bold text-violet-700"
      >
        {initial}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-56 rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
          <div className="px-3 py-2">
            <p className="truncate text-sm font-medium">{me.email}</p>
            <p className="text-xs text-slate-500">
              {me.role ? ROLE_LABEL[me.role] : "역할 없음"}
            </p>
          </div>
          {me.role === "admin" && (
            <Link
              href="/admin/users"
              className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
            >
              사용자 관리
            </Link>
          )}
          {/* POST인 이유: GET 로그아웃은 링크 프리페치에 걸려 의도치 않게
              세션을 끊을 수 있다. */}
          <form action="/api/auth/logout" method="post">
            <button
              type="submit"
              className="w-full rounded-lg px-3 py-2 text-left text-sm text-rose-700 hover:bg-rose-50"
            >
              로그아웃
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: `AppHeader.tsx`를 수정한다**

`frontend/components/AppHeader.tsx`에서 하드코딩된 버튼(65–70행 — `aria-label="사용자 메뉴"`인 그 `<button>`)을 교체한다.

**(a)** 파일 상단에 import 추가:

```tsx
import { UserMenu } from "./UserMenu";
```

**(b)** 다음 블록을

```tsx
          <button
            className="w-9 h-9 rounded-full bg-violet-100 text-violet-700 font-bold text-sm"
            aria-label="사용자 메뉴"
          >
            김PM
          </button>
```

이것으로 바꾼다:

```tsx
          <UserMenu />
```

- [ ] **Step 6: 기존 `AppHeader` 테스트에 하드코딩 단정이 있는지 확인한다**

```bash
cd frontend && grep -n "김PM\|사용자 메뉴" components/AppHeader.test.tsx
```

출력이 있으면 그 단정을 지운다. `UserMenu`는 `/api/auth/me`를 부르므로
`AppHeader` 테스트에서 MSW 미처리 요청 오류가 날 수 있다 — 그 경우
`test/msw/handlers.ts`의 기본 핸들러에 다음을 추가한다:

```typescript
  // UserMenu가 모든 화면에서 부른다 — 기본은 "미인증"으로 두어 화면 테스트가
  // 사용자 메뉴를 신경쓰지 않게 한다.
  http.get("*/api/auth/me", () => HttpResponse.json({ authenticated: false },
                                                    { status: 401 })),
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd frontend && npx vitest run`
Expected: 전체 통과 (신규 login 8 + UserMenu 6 포함)

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 8: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/app/login frontend/components/UserMenu.tsx \
        frontend/components/UserMenu.test.tsx frontend/components/AppHeader.tsx \
        frontend/components/AppHeader.test.tsx frontend/test/msw/handlers.ts
git commit -m "$(cat <<'EOF'
feat(auth): 로그인 화면 + 헤더 사용자 메뉴 (하드코딩 "김PM" 제거)

토큰이 httpOnly 쿠키라 JS가 읽을 수 없으므로 표시용 정보는 /api/auth/me에 묻는다.
관리자에게만 사용자 관리 링크를 노출한다(UX — 실제 차단은 백엔드 403).

로그인 버튼은 Link가 아니라 a다: /api/auth/login은 외부 리다이렉트를 내는 route
handler이고 클라이언트 라우팅으로는 따라갈 수 없다.
로그아웃은 POST다: GET이면 링크 프리페치가 세션을 끊을 수 있다.

오류 코드는 화이트리스트로만 문구를 고른다 — 쿼리 파라미터를 그대로 렌더하면
반사형 XSS 표면이 된다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: 호스팅 스택 배선 — 콜백 주입 · env · IAM

**Files:**
- Modify: `infra/lib/pathfinder-hosting-stack.ts`
- Modify: `infra/lib/user-data.ts`
- Modify: `infra/bin/app.ts`
- Modify: `infra/test/user-data.assert.ts`
- Modify: `infra/test/hosting-stack.assert.ts`

**Interfaces:**
- Consumes: Task 1의 상수, Task 3의 `PathfinderAuthStack`
- Produces: `HostingStackProps`에 `userPool`, `userPoolClient`, `hostedUiDomain` 추가

- [ ] **Step 1: `user-data.assert.ts`에 실패하는 단정을 추가한다**

기존 파일 끝에 다음을 추가한다:

```typescript
// --- 인증 배선 (Cognito) ---
{
  const script = renderUserData({
    region: 'ap-northeast-2',
    bucketName: 'bucket-x',
    model: 'model-x',
    secretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:abc',
    assetS3Uri: 's3://b/k.zip',
    userPoolId: 'ap-northeast-2_POOL',
    userPoolClientId: 'client-abc',
    hostedUiDomain: 'pathfinder-x.auth.ap-northeast-2.amazoncognito.com',
    appUrl: 'https://d123.cloudfront.net',
  });

  // 백엔드: 이 두 값이 있어야 인증이 켜진다(없으면 바이패스 = 무인증 공개).
  assert.ok(script.includes('PATHFINDER_COGNITO_USER_POOL_ID=ap-northeast-2_POOL'),
    'backend must receive the user pool id — without it auth is bypassed');
  assert.ok(script.includes('PATHFINDER_COGNITO_CLIENT_ID=client-abc'),
    'backend must receive the client id');

  // 프론트: Hosted UI 도메인·클라이언트·앱 URL.
  assert.ok(script.includes('COGNITO_HOSTED_UI_DOMAIN=pathfinder-x.auth.ap-northeast-2.amazoncognito.com'));
  assert.ok(script.includes('COGNITO_CLIENT_ID=client-abc'));
  assert.ok(script.includes('APP_BASE_URL=https://d123.cloudfront.net'));

  // 클라이언트 시크릿은 부팅 시 Cognito에서 조회한다 — 템플릿에 평문으로 남기지 않는다.
  assert.ok(script.includes('describe-user-pool-client'),
    'client secret must be fetched at boot, not baked into the template');
  assert.ok(!script.includes('COGNITO_CLIENT_SECRET=secret'),
    'a literal client secret must never appear in user-data');

  // NEXT_PUBLIC_ 접두어가 붙으면 시크릿이 클라이언트 번들로 나간다.
  assert.ok(!script.includes('NEXT_PUBLIC_COGNITO'),
    'Cognito env must never be NEXT_PUBLIC_ (it would be inlined into the browser bundle)');

  console.log('OK  user-data: cognito env for backend + frontend, secret fetched at boot');
}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd infra && npx ts-node test/user-data.assert.ts`
Expected: FAIL — `renderUserData`가 새 옵션을 모르므로 타입 에러 또는 단정 실패

- [ ] **Step 3: `user-data.ts`를 수정한다**

**(a)** `UserDataOptions`에 4개를 추가한다:

```typescript
export interface UserDataOptions {
  region: string;
  bucketName: string;
  model: string;
  secretArn: string;
  assetS3Uri: string;
  // 인증. 이 값들이 비면 백엔드가 인증 바이패스로 돌아 배포가 무인증으로
  // 공개된다 — 호스팅 스택이 항상 채운다.
  userPoolId: string;
  userPoolClientId: string;
  hostedUiDomain: string;
  appUrl: string;
}
```

**(b)** 구조분해를 수정한다:

```typescript
  const { region, bucketName, model, secretArn, assetS3Uri,
          userPoolId, userPoolClientId, hostedUiDomain, appUrl } = opts;
```

**(c)** 프론트 빌드 **앞**에 클라이언트 시크릿 조회를 넣는다. 기존
`# --- 프론트: 빌드 (same-origin /api) ---` 블록을 다음으로 교체한다:

```bash
# --- Cognito 앱 클라이언트 시크릿 (부팅 시 조회) ---
# CFN 템플릿에 평문으로 남기지 않기 위해 Cognito에서 직접 읽는다. Secrets Manager
# 사본을 두지 않는 이유: 시크릿은 Cognito가 만들었으므로 사본을 만들려면 값을
# CFN 경유로 옮겨야 하고, 그러면 템플릿에 남는다.
COGNITO_SECRET=$(aws cognito-idp describe-user-pool-client \\
  --user-pool-id ${userPoolId} --client-id ${userPoolClientId} \\
  --query 'UserPoolClient.ClientSecret' --output text --region ${region})

# --- 프론트: 빌드 (same-origin /api) ---
cd ${APP}/frontend
runuser -u ${SVC} -- env NEXT_PUBLIC_API_BASE_URL=/api HOME=${APP} npm ci
runuser -u ${SVC} -- env NEXT_PUBLIC_API_BASE_URL=/api HOME=${APP} npm run build
```

**(d)** 백엔드 systemd 유닛의 env 목록에 3줄을 추가한다
(`Environment=PATHFINDER_PROTO_CONFIG_DIR=${APP}/proto-config` 바로 뒤):

```bash
# 인증: 이 두 값이 비면 백엔드가 모든 요청을 통과시킨다(로컬 개발용 바이패스).
# 배포에서는 반드시 채워져야 한다.
Environment=PATHFINDER_COGNITO_USER_POOL_ID=${userPoolId}
Environment=PATHFINDER_COGNITO_CLIENT_ID=${userPoolClientId}
Environment=PATHFINDER_COGNITO_REGION=${region}
```

**(e)** 프론트 systemd 유닛의 env 목록에 4줄을 추가한다
(`Environment=HOME=${APP}` 바로 뒤):

```bash
# Hosted UI 왕복과 토큰 교환. NEXT_PUBLIC_ 접두어를 붙이면 안 된다 —
# 클라이언트 번들에 인라인되어 시크릿이 브라우저로 나간다.
Environment=COGNITO_HOSTED_UI_DOMAIN=${hostedUiDomain}
Environment=COGNITO_CLIENT_ID=${userPoolClientId}
Environment=COGNITO_CLIENT_SECRET=\${COGNITO_SECRET}
Environment=APP_BASE_URL=${appUrl}
```

> `\${COGNITO_SECRET}`의 이스케이프는 기존 nginx 블록의 `\${SECRET}`과 같은
> 패턴이다 — heredoc 안에서 셸 변수로 치환되어야 한다.

- [ ] **Step 4: 단정 통과를 확인한다**

Run: `cd infra && npx ts-node test/user-data.assert.ts`
Expected: 기존 `OK` 줄들 + 새 `OK  user-data: cognito env …`

- [ ] **Step 5: `hosting-stack.assert.ts`에 단정을 추가한다**

`makeHosting()` 헬퍼가 새 props를 넘기도록 고치고, 파일 끝에 추가한다:

```typescript
// --- 콜백 URL 주입 (순환 의존 해소) ---
{
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill3', { env: ENV });
  const auth = new PathfinderAuthStack(app, 'Auth3', { env: ENV });
  const hosting = new PathfinderHostingStack(app, 'Hosting3', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-1234',
    userPool: auth.userPool,
    userPoolClient: auth.userPoolClient,
    hostedUiDomain: auth.hostedUiDomain,
  });
  const t = Template.fromStack(hosting);
  const bodies = JSON.stringify(t.findResources('Custom::AWS'));

  // CloudFront 도메인을 콜백 URL에 등록하는 커스텀 리소스.
  assert.ok(bodies.includes('updateUserPoolClient'),
    'hosting must register the CloudFront callback URL with the app client');
  // PUT 시맨틱이므로 전체 설정을 다시 써야 한다 — 콜백만 보내면 나머지가 지워진다.
  assert.ok(bodies.includes('AllowedOAuthFlows'),
    'UpdateUserPoolClient has PUT semantics — the full client config must be resent');
  assert.ok(bodies.includes('LogoutURLs'), 'logout URLs must be resent too');
  // localhost 콜백도 유지돼야 로컬 개발이 깨지지 않는다.
  assert.ok(bodies.includes('http://localhost:3000/api/auth/callback'),
    'the localhost callback must survive the update');

  // 인스턴스 롤이 클라이언트 시크릿을 읽을 수 있어야 한다(부팅 시 조회).
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({ Action: 'cognito-idp:DescribeUserPoolClient' }),
      ]),
    },
  });
  console.log('OK  hosting stack: callback URL injection + full client config resend + secret read permission');
}
```

파일 상단에 import를 추가한다:

```typescript
import { PathfinderAuthStack } from '../lib/pathfinder-auth-stack';
```

- [ ] **Step 6: `pathfinder-hosting-stack.ts`를 수정한다**

**(a)** import 추가:

```typescript
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import {
  LOCAL_APP_URL, OAUTH_SCOPES, callbackUrls, logoutUrls,
} from './auth-client-config';
```

**(b)** `HostingStackProps`에 3개 추가:

```typescript
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
```

**(c)** `renderUserData` 호출에 4개를 넘긴다 — **단, `appUrl`이 문제다.**
CloudFront distribution은 user-data보다 뒤에 만들어지므로 도메인을 아직 모른다.
**해결: instance를 만들기 전에 distribution을 만들 수 없으므로(오리진이 EIP DNS를
필요로 함), `appUrl`은 EIP 기반 도메인이 아니라 CloudFront 도메인이어야 한다.**

이 순환은 `cdk.Lazy`로 끊는다. `userData` 생성을 distribution 생성 **뒤로** 옮기고
`instance` 생성도 그 뒤로 옮기는 것은 불가능하므로(distribution이 instance의 EIP를
참조), `appUrl`만 `Lazy.string`으로 늦게 해석시킨다:

```typescript
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
        assetS3Uri: asset.s3ObjectUrl,
        userPoolId: props.userPool.userPoolId,
        userPoolClientId: props.userPoolClient.userPoolClientId,
        hostedUiDomain: props.hostedUiDomain,
        appUrl: appUrlToken,
      }),
    );
```

그리고 distribution 생성 직후에 대입한다:

```typescript
    distributionDomain = distribution.distributionDomainName;
```

**(d)** 인스턴스 롤에 시크릿 조회 권한을 준다 (`headerSecret.grantRead(role);` 뒤):

```typescript
    // 부팅 시 클라이언트 시크릿을 Cognito에서 직접 읽는다(§3.4) — 템플릿에
    // 평문으로 남기지 않기 위한 선택이고, 그 대가가 이 권한이다.
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['cognito-idp:DescribeUserPoolClient'],
      resources: [props.userPool.userPoolArn],
    }));
```

**(e)** distribution 생성 뒤에 콜백 URL 주입 커스텀 리소스를 추가한다
(`new cdk.CfnOutput(this, 'DistributionDomain', …)` **앞**):

```typescript
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
```

- [ ] **Step 7: `bin/app.ts`를 Task 3 Step 5의 내용으로 되돌린다**

Task 3에서 되돌려 놓았던 변경을 지금 적용한다 (그 Step의 전체 파일 내용을 그대로 쓴다).

- [ ] **Step 8: 전체 인프라 테스트와 합성을 확인한다**

Run: `cd infra && npx tsc --noEmit && npm test`
Expected: 5개 assert 파일 전부 `OK …`

Run: `cd infra && npx cdk synth --quiet`
Expected: 성공 (CloudFront 프리픽스 리스트 lookup에 AWS 크리덴셜이 필요하다 —
없으면 `cdk.context.json`의 캐시를 쓴다. 캐시도 없고 크리덴셜도 없으면 이 단계는
건너뛰고 `npm test`의 결과로 대신한다.)

- [ ] **Step 9: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/pathfinder-hosting-stack.ts infra/lib/user-data.ts infra/bin/app.ts \
        infra/test/user-data.assert.ts infra/test/hosting-stack.assert.ts
git commit -m "$(cat <<'EOF'
feat(infra): 호스팅 스택에 인증 배선 — 콜백 주입 · env · 시크릿 조회

Cognito는 콜백 URL의 전수 일치만 허용하고 실제 URL은 이 스택이 만드는 CloudFront
도메인에 달려 있다. 배포 마지막에 UpdateUserPoolClient로 등록해 순환을 끊는다.

그 API는 PUT 시맨틱이라 콜백만 보내면 나머지 설정이 지워진다 — 클라이언트 설정
전체를 다시 쓰고, 값의 출처는 auth-client-config.ts 하나로 유지한다.

클라이언트 시크릿은 EC2가 부팅 시 describe-user-pool-client로 읽는다. 템플릿에
평문으로 남기지 않기 위한 선택이고 그 대가가 인스턴스 롤의 조회 권한이다.
Cognito env에는 NEXT_PUBLIC_을 붙이지 않는다(붙이면 시크릿이 브라우저 번들로 간다).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: SSE 401 → 로그인 화면

**Files:**
- Create: `frontend/lib/auth/sessionRecovery.ts`
- Create: `frontend/lib/auth/sessionRecovery.test.ts`
- Modify: `frontend/lib/useTurnStream.ts` (`onError` 콜백)
- Modify: `frontend/lib/usePrototypeStream.ts` (`onError` 콜백)
- Modify: `frontend/lib/useWorkspaceStream.ts` (`onError` 콜백)

**Interfaces:**
- Consumes: Task 11의 `/api/auth/me`
- Produces:
  - `async function redirectIfSessionExpired(navigate?: (url: string) => void): Promise<boolean>`
    — 세션이 끊겼으면 `/login`으로 보내고 `true`, 살아 있으면 `false`

**왜 필요한가 (스펙 §7):** `EventSource`는 응답 상태코드를 노출하지 않는다 —
401이든 네트워크 끊김이든 똑같이 `onerror`로만 온다. 지금 세 훅은 모두 "연결이
끊어졌습니다"를 보여주는데, 토큰이 만료된 경우 사용자는 재시도할 때마다 같은
메시지를 보며 왜 안 되는지 알 수 없다. 스트림 오류 뒤 세션을 한 번 확인해
만료라면 로그인으로 보낸다.

프록시의 리프레시가 스트림 요청에는 적용되지 않는 이유도 이것이다(Task 13:
`isRetryableWithRefresh`가 GET만 허용하지만 SSE는 응답 본문을 이미 스트리밍
중이라 재시도가 무의미하다). 그 빈틈을 이 Task가 메운다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/lib/auth/sessionRecovery.test.ts`:

```typescript
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/msw/server";
import { redirectIfSessionExpired } from "./sessionRecovery";

function mockMe(body: unknown, status: number) {
  server.use(http.get("*/api/auth/me", () => HttpResponse.json(body, { status })));
}

describe("redirectIfSessionExpired", () => {
  it("navigates to /login when the session is gone", async () => {
    mockMe({ authenticated: false }, 401);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(true);
    expect(navigate).toHaveBeenCalledWith("/login");
  });

  it("does nothing when the session is still valid", async () => {
    // 진짜 네트워크 끊김 — 훅의 기존 "연결이 끊어졌습니다" 메시지가 맞는 상황이다.
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" }, 200);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate when the check itself fails", async () => {
    // /api/auth/me까지 닿지 않는 상황에서 로그인으로 보내면, 백엔드가 잠깐
    // 죽은 것뿐인데 사용자를 작업 중인 화면에서 쫓아낸다.
    server.use(http.get("*/api/auth/me", () => HttpResponse.error()));
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("preserves the current path so login can return there", async () => {
    mockMe({ authenticated: false }, 401);
    const navigate = vi.fn();
    await redirectIfSessionExpired(navigate, "/projects/p1/canvas");
    expect(navigate).toHaveBeenCalledWith(
      "/login?next=%2Fprojects%2Fp1%2Fcanvas");
  });
});
```

- [ ] **Step 2: 실패를 확인한 뒤 구현한다**

Run: `cd frontend && npx vitest run lib/auth/sessionRecovery`
Expected: FAIL — 모듈 없음

`frontend/lib/auth/sessionRecovery.ts`:

```typescript
// frontend/lib/auth/sessionRecovery.ts
//
// EventSource는 응답 상태코드를 노출하지 않는다 — 401(토큰 만료)이든 네트워크
// 끊김이든 똑같이 onerror로만 온다. 스트림이 죽은 뒤 세션을 한 번 확인해서
// 만료라면 로그인으로 보낸다. 그러지 않으면 사용자는 "연결이 끊어졌습니다"를
// 반복해서 보며 왜 안 되는지 알 수 없다.
//
// /api 프록시의 리프레시가 스트림에는 적용되지 않는 이유도 같다: SSE는 응답을
// 이미 스트리밍 중이라 401을 받은 시점에 재시도가 무의미하다.

// navigate를 주입받는 이유: 훅에서는 next/navigation의 router.push를 넘기고,
// 테스트에서는 스파이를 넘긴다. 기본값은 전체 페이지 이동 —
// 로그인 왕복은 어차피 앱 상태를 버리므로 클라이언트 라우팅의 이점이 없다.
function defaultNavigate(url: string): void {
  window.location.assign(url);
}

export async function redirectIfSessionExpired(
  navigate: (url: string) => void = defaultNavigate,
  currentPath?: string,
): Promise<boolean> {
  let alive: boolean;
  try {
    const res = await fetch("/api/auth/me", { credentials: "include" });
    alive = res.ok;
  } catch {
    // 확인 자체가 실패했다 — 백엔드가 잠깐 죽은 것뿐일 수 있으므로 사용자를
    // 작업 중인 화면에서 쫓아내지 않는다.
    return false;
  }
  if (alive) return false;
  const next = currentPath
    ? `/login?next=${encodeURIComponent(currentPath)}`
    : "/login";
  navigate(next);
  return true;
}
```

Run: `cd frontend && npx vitest run lib/auth/sessionRecovery`
Expected: 4 passed

- [ ] **Step 3: 세 훅의 `onError`에 세션 확인을 붙인다**

각 파일에 import를 추가한다:

```typescript
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
```

그리고 `onError` 콜백 본문 **첫 줄**에 다음을 넣는다 (기존 동작은 그대로 둔다 —
세션이 살아 있으면 지금까지와 똑같이 "연결이 끊어졌습니다"가 보인다):

```typescript
        onError: () => {
          // 401(토큰 만료)과 네트워크 끊김을 EventSource가 구분해주지 않으므로
          // 세션을 확인해 만료면 로그인으로 보낸다. 살아 있으면 아래 메시지가 맞다.
          void redirectIfSessionExpired(undefined, window.location.pathname);
          // ... 기존 본문 그대로 ...
        },
```

**`frontend/lib/useTurnStream.ts`** (134행):

```typescript
        onError: () => {
          void redirectIfSessionExpired(undefined, window.location.pathname);
          patchAi(aiId, (it) => ({
            ...it,
            streaming: false,
            error: it.error ?? "연결이 끊어졌습니다. 다시 시도해 주세요.",
          }));
          finish();
        },
```

**`frontend/lib/usePrototypeStream.ts`** (123행)과
**`frontend/lib/useWorkspaceStream.ts`** (190행)도 같은 방식으로 `onError` 본문
첫 줄에 같은 한 줄을 추가한다. 기존 본문은 손대지 않는다.

- [ ] **Step 4: 기존 훅 테스트가 깨지지 않았는지 확인한다**

세 훅에는 이미 테스트가 있다(`useTurnStream.test.tsx` 등). `onError` 경로를
지나는 테스트가 `/api/auth/me` 미처리 요청으로 실패할 수 있다 — Task 16 Step 6에서
`test/msw/handlers.ts`에 기본 핸들러를 넣었으므로 통과해야 한다. 그래도 실패하면
그 기본 핸들러가 있는지 확인한다.

Run: `cd frontend && npx vitest run lib/useTurnStream lib/usePrototypeStream lib/useWorkspaceStream`
Expected: 전부 통과

- [ ] **Step 5: 전체 프론트 스위트**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/auth/sessionRecovery.ts frontend/lib/auth/sessionRecovery.test.ts \
        frontend/lib/useTurnStream.ts frontend/lib/usePrototypeStream.ts \
        frontend/lib/useWorkspaceStream.ts
git commit -m "$(cat <<'EOF'
feat(auth): SSE 오류 뒤 세션을 확인해 만료면 로그인으로 보낸다

EventSource는 응답 상태코드를 노출하지 않아 401(토큰 만료)과 네트워크 끊김이
똑같이 onerror로 온다. 확인하지 않으면 사용자는 "연결이 끊어졌습니다"를 반복해서
보며 왜 안 되는지 알 수 없다.

프록시의 리프레시가 스트림에 적용되지 않는 빈틈을 메운다 — SSE는 응답을 이미
스트리밍 중이라 401 시점에 재시도가 무의미하다.

확인 자체가 실패하면 이동하지 않는다: 백엔드가 잠깐 죽은 것뿐일 수 있는데
작업 중인 화면에서 사용자를 쫓아내면 안 된다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: 문서 + 수동 e2e 체크리스트

**Files:**
- Modify: `README.md`
- Modify: `infra/README.md`
- Modify: `backend/.env.example`
- Modify: `frontend/.env.local.example`
- Create: `docs/superpowers/checklists/2026-07-25-cognito-auth-e2e.md`

**Interfaces:**
- Consumes: 앞선 모든 Task
- Produces: 없음 (문서)

- [ ] **Step 1: `backend/.env.example`에 인증 env를 추가한다**

파일 끝에 추가한다:

```bash
# ---- 인증 (Cognito) ----
# 비워두면 인증이 전체 바이패스된다 — 로컬 개발과 테스트의 기본 상태다.
# 값은 `cd infra && npx cdk deploy PathfinderAuthStack` 출력에서 가져온다.
#   UserPoolId       -> PATHFINDER_COGNITO_USER_POOL_ID
#   UserPoolClientId -> PATHFINDER_COGNITO_CLIENT_ID
# 두 값이 모두 있어야 인증이 켜진다(하나만 있으면 미설정으로 취급).
PATHFINDER_COGNITO_USER_POOL_ID=
PATHFINDER_COGNITO_CLIENT_ID=
# 미지정 시 PATHFINDER_S3_REGION을 따른다.
PATHFINDER_COGNITO_REGION=
```

- [ ] **Step 2: `frontend/.env.local.example`에 인증 env를 추가한다**

```bash
# ---- 인증 (Cognito) — 서버사이드 전용 ----
# ⚠️ NEXT_PUBLIC_ 접두어를 붙이지 말 것. 붙이면 클라이언트 번들에 인라인되어
# client secret이 브라우저로 나간다. route handler와 middleware만 이 값을 읽는다.
#
# 값의 출처:
#   HostedUiDomain   (AuthStack 출력)          -> COGNITO_HOSTED_UI_DOMAIN
#   UserPoolClientId (AuthStack 출력)          -> COGNITO_CLIENT_ID
#   클라이언트 시크릿은 콘솔 또는
#     aws cognito-idp describe-user-pool-client \
#       --user-pool-id <pool> --client-id <client> \
#       --query UserPoolClient.ClientSecret --output text
#
# 로컬에서 인증을 켜고 검증하려면 NEXT_PUBLIC_API_BASE_URL=/api 여야 한다 —
# 쿠키는 same-origin에서만 프록시를 타고 백엔드로 번역된다.
COGNITO_HOSTED_UI_DOMAIN=
COGNITO_CLIENT_ID=
COGNITO_CLIENT_SECRET=
APP_BASE_URL=http://localhost:3000
```

- [ ] **Step 3: `README.md`를 수정한다**

**(a)** 아키텍처 블록(README의 ```` ``` ````로 감싼 `frontend/ backend/ infra/` 3줄)을 다음으로 바꾼다:

```
frontend/  Next.js 15 (App Router) — 대시보드 · 질문 위저드 · 문서 리뷰 · 대화형 캔버스 · 프로토타입 탭 · 로그인/사용자 관리
backend/   FastAPI — 파서 · 인프로세스 Strands 에이전트 · SSE 턴 릴레이 · S3 영속화 · 프로토타입 빌드/호스팅 · JWT 검증
infra/     CDK (TypeScript) — S3 버킷 + 백엔드 실행 롤 + Cognito(Hosted UI v2) + EC2/CloudFront (서울, 리전 파라미터화)
```

**(b)** "환경 변수 요약"의 백엔드 표에 3행을 추가한다:

```
| `PATHFINDER_COGNITO_USER_POOL_ID` | — | Cognito 풀 id. **비우면 인증 전체 바이패스**(로컬/테스트 기본) |
| `PATHFINDER_COGNITO_CLIENT_ID` | — | 앱 클라이언트 id. access 토큰의 `client_id` 클레임 검증용 |
| `PATHFINDER_COGNITO_REGION` | `PATHFINDER_S3_REGION` | 풀이 있는 리전 |
```

프론트엔드 표에 4행을 추가한다:

```
| `COGNITO_HOSTED_UI_DOMAIN` | — | Hosted UI 도메인 (server-side only) |
| `COGNITO_CLIENT_ID` | — | 앱 클라이언트 id (server-side only) |
| `COGNITO_CLIENT_SECRET` | — | 토큰 교환용 시크릿. **`NEXT_PUBLIC_` 금지** |
| `APP_BASE_URL` | `http://localhost:3000` | 콜백 URL 조립용 |
```

**(c)** "참고" 절의 **"인증은 아직 플레이스홀더다"** 항목(3줄, `getAuthToken()`을 언급하는 그 항목)을 다음으로 **교체**한다:

```markdown
- **인증은 Amazon Cognito(Hosted UI v2)** 다. 역할은 `admin`과 `pm` 둘이며 Cognito
  그룹 멤버십이 역할의 유일한 출처다. **self-signup은 차단**되어 있어 신규 계정은
  `/admin/users`에서 관리자가 초대해야 생긴다(초대하면 임시 비밀번호가 화면에 1회
  표시된다 — 이 앱은 메일을 보내지 않는다).

  세션은 **httpOnly 쿠키**에 담기고 same-origin `/api` 프록시가 그것을
  `Authorization: Bearer`로 번역한다. `EventSource`는 커스텀 헤더를 못 보내지만
  쿠키는 자동 전송되므로 SSE도 이 경로로 인증된다.

  **무인증으로 열려 있는 경로는 둘뿐이다**: `/survey/{token}`(익명 설문)과
  `/proto/{pid}/{slug}/*`(프로토타입 프리뷰). 둘 다 계정이 없는 최종 사용자를
  위한 것이며, `backend/tests/test_auth_route_coverage.py`가 이 경계를 강제한다.

  **로컬 개발**은 `PATHFINDER_COGNITO_USER_POOL_ID`를 비워두면 인증이 전체
  바이패스되어 지금까지와 똑같이 돈다. 로컬에서 인증을 켜고 검증하려면
  `NEXT_PUBLIC_API_BASE_URL=/api`로 띄워야 한다(쿠키는 same-origin에서만 프록시를
  타고 번역된다).
```

**(d)** "실행 방법" 절의 `npx cdk deploy` 명령을 `--all`로 바꾼다:

```bash
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2   # 계정·리전 최초 1회 (기본 서울)
npx cdk deploy --all --require-approval never
```

그리고 그 아래 CfnOutputs 목록(`ArtifactsBucketName → …` 블록)에 추가한다:

```
UserPoolId          → PATHFINDER_COGNITO_USER_POOL_ID
UserPoolClientId    → PATHFINDER_COGNITO_CLIENT_ID / COGNITO_CLIENT_ID
HostedUiDomain      → COGNITO_HOSTED_UI_DOMAIN
DistributionDomain  → APP_BASE_URL (그리고 브라우저로 접속할 주소)
```

**(e)** 배포 직후 로그인 안내를 CfnOutputs 블록 뒤에 추가한다:

```markdown
배포가 끝나면 `DistributionDomain`으로 접속해 아래 시드 계정으로 로그인한다:

| 계정 | 역할 | 비밀번호 |
|---|---|---|
| `admin@pathfinder.local` | 관리자 (사용자 관리 가능) | `PathFinder2026!@` |
| `pm@pathfinder.local` | PM | `PathFinder2026!@` |

> ⚠️ **이 비밀번호는 데모/워크숍용이다.** CDK 소스의 상수이므로 CloudFormation
> 템플릿과 스택 이벤트에 평문으로 남고, 계정에 CFN 읽기 권한이 있는 사람은 누구나
> 볼 수 있다. 재배포하면 이 값으로 되돌아간다. 실제 운영에 쓰려면
> `infra/lib/auth-client-config.ts`의 `SEED_PASSWORD`를 교체하고, 시드 계정 대신
> `/admin/users`에서 초대한 계정을 쓴다.
```

**(f)** "테스트" 절의 `cd infra && npx cdk synth` 줄을 다음으로 바꾼다:

```bash
# 인프라 합성 + 템플릿 단정 (배포 없이 검증)
cd infra && npm test
```

- [ ] **Step 4: `infra/README.md`에 인증 스택을 문서화한다**

파일에 다음 절을 추가한다 (기존 스택 설명 뒤):

```markdown
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

### 콜백 URL 순환 의존

Cognito는 콜백 URL의 전수 일치만 허용하고(와일드카드 불가) 실제 URL은
HostingStack이 만드는 CloudFront 도메인에 달려 있다. AuthStack은 localhost 콜백만
갖고 배포되고, HostingStack이 배포 마지막에 `UpdateUserPoolClient`로 실제 도메인을
등록한다.

⚠️ **그 API는 PUT 시맨틱이다** — 지정하지 않은 필드를 지운다. 따라서 콜백만 보내는
것이 아니라 클라이언트 설정 전체를 다시 쓴다. 값의 출처는
`lib/auth-client-config.ts` 하나뿐이라 AuthStack과 어긋나지 않는다.

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
```

- [ ] **Step 5: 수동 e2e 체크리스트를 만든다**

`docs/superpowers/checklists/2026-07-25-cognito-auth-e2e.md`:

```markdown
# 인증 + 사용자 관리 수동 e2e 검증

날짜: 2026-07-25
대상: `docs/superpowers/plans/2026-07-25-cognito-auth-user-management.md`

실 AWS 배포가 필요하다(Cognito·CloudFront·EC2). 유닛 테스트로는 검증할 수 없는
것 — Hosted UI v2의 실제 렌더, 시드 계정의 로그인 가능 여부, 쿠키가 SSE까지
흐르는지 — 만 여기서 확인한다.

## 준비

```bash
cd infra
npx cdk deploy --all --require-approval never
```

출력에서 `DistributionDomain`을 적어둔다. 부팅에 5–10분 걸린다
(`aws ssm start-session` → `tail -f /var/log/pathfinder-bootstrap.log`로 확인).

## 체크리스트

- [ ] **1. 미인증 접근이 로그인으로 리다이렉트된다**
  `DistributionDomain`을 시크릿 창으로 열면 `/login?next=%2F`로 이동한다.

- [ ] **2. Hosted UI v2가 정상 렌더된다**
  "로그인" 버튼 → Cognito 로그인 화면. **깨진 레이아웃이 아니어야 한다**
  (v2는 브랜딩 레코드가 없으면 렌더가 깨진다 — `CfnManagedLoginBranding` 검증).

- [ ] **3. 회원가입 링크가 없다**
  로그인 화면에 "Sign up" 링크가 **보이지 않는다**(self-signup 차단의 육안 확인).

- [ ] **4. 시드 관리자 계정이 즉시 로그인된다**
  `admin@pathfinder.local` / `PathFinder2026!@` → **비밀번호 변경을 요구하지 않고**
  곧바로 프로젝트 목록으로 들어간다(`Permanent: true` 검증).

- [ ] **5. 헤더에 실제 사용자가 보인다**
  우상단 아바타가 `A`이고, 클릭하면 `admin@pathfinder.local` / `관리자` /
  `사용자 관리` / `로그아웃`이 보인다(하드코딩 "김PM"이 사라졌는지 확인).

- [ ] **6. 기존 기능이 인증 뒤에서 정상 동작한다**
  프로젝트 생성 → 캔버스에서 메시지 전송 → **SSE 응답이 스트리밍된다.**
  이것이 쿠키→Bearer 번역의 실증이다(실패하면 스트림이 401로 끊긴다).

- [ ] **7. 초대가 동작한다**
  `/admin/users` → "사용자 초대" → 이메일 입력, 역할 `pm` → 임시 비밀번호가
  화면에 표시된다. 복사 버튼이 동작한다.

- [ ] **8. 초대된 계정의 첫 로그인이 비밀번호 변경을 요구한다**
  로그아웃 후 새 계정 + 임시 비밀번호로 로그인 → Hosted UI가 새 비밀번호를
  요구한다(`Permanent: false` 검증). 변경 후 프로젝트 목록으로 들어간다.

- [ ] **9. pm은 사용자 관리에 접근할 수 없다**
  - 헤더 메뉴에 "사용자 관리" 링크가 **없다**.
  - 주소창에 `/admin/users`를 직접 입력 → 프로젝트 목록으로 되돌아간다(미들웨어).
  - (심화) 브라우저 콘솔에서 `fetch('/api/admin/users').then(r=>r.status)` →
    **403**. 미들웨어를 우회해도 백엔드가 막는다는 확인.

- [ ] **10. 마지막 관리자 보호가 동작한다**
  admin으로 로그인 → `/admin/users` → 자기 행의 역할을 `PM`으로 바꾸려 하면
  "자신의 계정은 강등할 수 없습니다" 메시지가 뜬다. 삭제·비활성화도 같다.

- [ ] **11. 비밀번호 재설정이 동작한다**
  pm 계정의 "비밀번호 재설정" → 새 임시 비밀번호 표시 → 그 값으로 로그인되고
  변경을 요구한다.

- [ ] **12. 비활성화된 계정은 로그인할 수 없다**
  pm 계정을 "비활성화" → 그 계정으로 로그인 시도 → Hosted UI가 거부한다.

- [ ] **13. 익명 설문이 로그아웃 상태에서 동작한다**
  프로토타입 탭에서 설문을 만들어 링크를 복사 → 시크릿 창에서 열어 **로그인 없이**
  응답을 제출한다.

- [ ] **14. 프로토타입 프리뷰가 로그아웃 상태에서 열린다**
  호스팅 중인 프로토타입 URL을 시크릿 창에서 열면 앱이 뜬다.

- [ ] **15. 로그아웃이 Cognito 세션까지 끊는다**
  로그아웃 → 다시 "로그인" → **비밀번호를 다시 묻는다**(곧바로 통과하면
  Cognito 세션이 남은 것이다).

- [ ] **16. 재배포가 시드 비밀번호를 되돌린다**
  admin 계정의 비밀번호를 임의로 바꾼 뒤 `npx cdk deploy --all` → 다시
  `PathFinder2026!@`로 로그인된다(`onUpdate` 검증). 재배포가 사용자를 지우거나
  스택을 롤백하지 않는다(`ignoreErrorCodesMatching` 검증).

## 정리

```bash
cd infra && npx cdk destroy --all
```

⚠️ User Pool이 함께 삭제되어 **초대한 사용자 전원이 사라진다.**
```

- [ ] **Step 6: 문서의 링크와 사실관계를 확인한다**

```bash
cd /home/ec2-user/project/pathfinder-sp
# 스펙/플랜/체크리스트 상호 참조가 실제 파일을 가리키는지
for f in docs/superpowers/specs/2026-07-25-cognito-auth-user-management-design.md \
         docs/superpowers/plans/2026-07-25-cognito-auth-user-management.md \
         docs/superpowers/checklists/2026-07-25-cognito-auth-e2e.md; do
  test -f "$f" && echo "OK  $f" || echo "MISSING  $f"
done
# README에 "인증은 아직 플레이스홀더" 문구가 남아 있지 않은지
grep -n "플레이스홀더" README.md && echo "^^ 위 문구를 교체해야 한다" || echo "OK  README 갱신됨"
# getAuthToken 잔재 확인
grep -rn "getAuthToken" README.md frontend --include=*.ts --include=*.tsx --include=*.md \
  | grep -v node_modules && echo "^^ 잔재 있음" || echo "OK  getAuthToken 잔재 없음"
```

Expected: 세 파일 모두 `OK`, README 갱신됨, 잔재 없음

- [ ] **Step 7: 전체 테스트를 한 번 더 돌린다**

```bash
cd /home/ec2-user/project/pathfinder-sp
cd backend && .venv/bin/python -m pytest -q && cd ..
cd frontend && npx vitest run && npx tsc --noEmit && cd ..
cd infra && npx tsc --noEmit && npm test && cd ..
```
Expected: 세 스위트 전부 통과

- [ ] **Step 8: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add README.md infra/README.md backend/.env.example frontend/.env.local.example \
        docs/superpowers/checklists/2026-07-25-cognito-auth-e2e.md
git commit -m "$(cat <<'EOF'
docs: 인증 절차 · env · 시드 계정 경고 · 수동 e2e 체크리스트

README의 "인증은 아직 플레이스홀더" 항목을 실제 동작 설명으로 교체한다:
역할 2개, self-signup 차단, 쿠키→Bearer 번역(SSE 인증의 근거), 공개 경로 2개,
로컬 바이패스.

시드 비밀번호가 CloudFormation 템플릿에 평문으로 남는다는 경고를 README와
infra/README 양쪽에 남긴다 — 데모용이며 운영 전환 시 교체해야 한다.

체크리스트는 유닛 테스트로 검증할 수 없는 것만 다룬다: Hosted UI v2의 실제 렌더,
시드 계정이 비밀번호 변경 없이 로그인되는지, 쿠키가 SSE까지 흐르는지,
재배포가 시드 비밀번호를 되돌리는지.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 완료 기준

모든 Task가 끝나면 다음이 성립한다:

**자동 검증 (배포 없이)**
```bash
cd backend && .venv/bin/python -m pytest -q      # 기존 + 신규 전부 통과
cd frontend && npx vitest run && npx tsc --noEmit
cd infra && npx tsc --noEmit && npm test         # 5개 assert 파일
```

**요구사항 대응**

| 요구사항 | 구현 | 검증 |
|---|---|---|
| Cognito + Hosted UI v2 | Task 3 (`ManagedLoginVersion.NEWER_MANAGED_LOGIN` + 브랜딩) | `auth-stack.assert.ts`, 체크리스트 2 |
| 역할 admin / pm | Task 3 그룹 2개, Task 4–5 검증·의존성 | `test_auth_verifier.py`, `test_auth_deps.py` |
| self-signup 차단 | Task 3 `selfSignUpEnabled: false` | `auth-stack.assert.ts`, 체크리스트 3 |
| CDK 배포로 계정 2개 + 비밀번호 사전 설정 | Task 2·3 `seedUser()` | `auth-stack.assert.ts`, 체크리스트 4·16 |
| 초대만으로 신규 가입 | Task 9 `POST /admin/users` | `test_routes_admin_users.py`, 체크리스트 7·8 |
| 관리 페이지 + 사용자 관리 | Task 15 `/admin/users` | `UserTable.test.tsx`, `page.test.tsx`, 체크리스트 7·10–12 |
| 세션 만료 시 재로그인 유도 | Task 13 (리프레시), Task 18 (SSE) | `proxyAuth.test.ts`, `sessionRecovery.test.ts` |

**남는 알려진 한계 (스펙 §5.3·§9에 기록됨)**
- `/proto/*`는 `pid`·`slug`를 아는 사람이면 접근 가능 (현상 유지, 의도된 것)
- 미들웨어는 쿠키 서명을 검증하지 않는다 (UX 게이트, 방어선은 백엔드)
- 시드 비밀번호가 CFN 템플릿에 평문으로 남는다 (데모용, 운영 전 교체)
- MFA·소셜 IdP·자가 비밀번호 재설정·감사 로그는 범위 밖
