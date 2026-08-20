import * as assert from 'node:assert';
import {
  usernameForEmail,
  CALLBACK_PATH, GROUP_ADMIN, GROUP_PM, LOCAL_APP_URL, LOGOUT_PATH, OAUTH_SCOPES,
  SEED_ADMIN_EMAIL, SEED_PASSWORD, SEED_PM_EMAIL,
  ACCESS_TOKEN_VALIDITY_MINUTES, ID_TOKEN_VALIDITY_MINUTES,
  REFRESH_TOKEN_VALIDITY_MINUTES,
  callbackUrls, logoutUrls,
} from '../lib/auth-client-config';

// --- 토큰 유효기간: 프로토타입 빌드 한 번을 여유롭게 덮어야 한다.
//
// 실측 결함: 빌드 중 세션이 만료돼 로그아웃됐다. 1차 원인은 갱신이 백엔드
// 401에만 반응하는데 열려 있는 SSE 연결은 401을 만들지 못한다는 것이었고,
// 그쪽은 주기 갱신(frontend/lib/auth/keepSessionAlive.ts + /api/auth/refresh)으로
// 고쳤다. 이 상수는 **두 번째 방어선**이다: 갱신이 몇 번 연속 실패해도
// (네트워크 단절, Cognito 일시 오류) 빌드 한 번이 한 토큰 수명 안에서 끝나면
// 사용자는 만료를 겪지 않는다.
//
// 빌드는 보통 1시간 이내다. access 60분은 그 경계와 정확히 겹쳐 여유가 0이므로
// 최소 2배를 요구한다. Cognito의 access/id 상한은 24시간이다.
assert.ok(ACCESS_TOKEN_VALIDITY_MINUTES >= 120,
  'access token must outlast a full prototype build (~1h) with margin — 60m leaves none');
assert.ok(ACCESS_TOKEN_VALIDITY_MINUTES <= 24 * 60,
  'Cognito caps access-token validity at 24 hours');
// id 토큰은 access와 같은 수명을 유지한다. 짧은 쪽이 먼저 만료되면 /api/auth/me가
// 이메일·역할을 잃어 헤더가 비어 보이는데, 세션은 살아 있으므로 원인을 찾기 어렵다.
assert.strictEqual(ID_TOKEN_VALIDITY_MINUTES, ACCESS_TOKEN_VALIDITY_MINUTES,
  'id token must expire with the access token — a shorter one empties the header while the session lives');
// refresh 창은 갱신이 가능한 전체 기간이다. 이것이 끝나면 주기 갱신도 무력하다.
assert.strictEqual(REFRESH_TOKEN_VALIDITY_MINUTES, 60 * 24 * 30);
console.log('OK  auth-client-config: token validity outlasts a prototype build');

// 시드 비밀번호는 스펙이 못박은 값이다. 오타가 나면 배포는 성공하고 로그인만
// 실패하므로(디버깅이 어렵다) 상수 자체를 단정한다.
assert.strictEqual(SEED_PASSWORD, 'AiPdsWeb2026@!');
assert.strictEqual(SEED_ADMIN_EMAIL, 'admin@aipds.local');
assert.strictEqual(SEED_PM_EMAIL, 'pm@aipds.local');

// Task 3이 이 값으로 Cognito 그룹을 만들고 Task 4의 토큰 검증이 같은 문자열을
// cognito:groups 클레임과 맞춘다 — 오타는 배포까지 통과한 뒤 런타임 권한
// 오류로만 드러나므로 상수 자체를 단정한다.
assert.strictEqual(GROUP_ADMIN, 'admin');
assert.strictEqual(GROUP_PM, 'pm');

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
console.log('OK  auth-client-config: seed constants + group constants + callback/logout URL derivation');

// --- usernameForEmail: Cognito가 email-alias 풀에서 이메일 형식 Username을
// 거부한다("Username cannot be of email format, since user pool is configured
// for email alias"). 실측: 이 규칙을 몰라 시드 계정 생성이 스택 롤백을 냈다.
// backend/aipds/auth/cognito.py의 username_for_email과 같은 규칙이어야
// 한다 — 어긋나면 초대 계정과 시드 계정의 Username 규칙이 갈린다.
assert.strictEqual(usernameForEmail('admin@aipds.local'), 'admin');
assert.strictEqual(usernameForEmail('pm@aipds.local'), 'pm');
// 시드 상수에 실제로 적용했을 때의 값 — 이게 배포되는 Username이다.
assert.strictEqual(usernameForEmail(SEED_ADMIN_EMAIL), 'admin');
assert.strictEqual(usernameForEmail(SEED_PM_EMAIL), 'pm');
// 불변식: 결과에 '@'가 절대 없어야 한다(Cognito가 거부하는 유일한 조건).
for (const email of [SEED_ADMIN_EMAIL, SEED_PM_EMAIL, 'a+tag@x.io', 'Mixed@X.IO']) {
  assert.ok(!usernameForEmail(email).includes('@'),
    `usernameForEmail(${email}) must not be email-shaped`);
}
// 대소문자·공백 정규화, Cognito가 안 받는 문자 치환.
assert.strictEqual(usernameForEmail('  Mixed@X.IO  '), 'mixed');
assert.strictEqual(usernameForEmail('a+tag@x.io'), 'a-tag');
console.log('OK  usernameForEmail: local-part rule, no email-shaped username, normalization');
