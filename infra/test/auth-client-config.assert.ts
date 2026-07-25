import * as assert from 'node:assert';
import {
  CALLBACK_PATH, GROUP_ADMIN, GROUP_PM, LOCAL_APP_URL, LOGOUT_PATH, OAUTH_SCOPES,
  SEED_ADMIN_EMAIL, SEED_PASSWORD, SEED_PM_EMAIL,
  callbackUrls, logoutUrls,
} from '../lib/auth-client-config';

// 시드 비밀번호는 스펙이 못박은 값이다. 오타가 나면 배포는 성공하고 로그인만
// 실패하므로(디버깅이 어렵다) 상수 자체를 단정한다.
assert.strictEqual(SEED_PASSWORD, 'PathFinder2026!@');
assert.strictEqual(SEED_ADMIN_EMAIL, 'admin@pathfinder.local');
assert.strictEqual(SEED_PM_EMAIL, 'pm@pathfinder.local');

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
