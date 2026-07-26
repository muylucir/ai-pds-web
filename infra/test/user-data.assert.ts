import * as assert from 'node:assert';
import { renderUserData } from '../lib/user-data';

const s = renderUserData({
  region: 'ap-northeast-2',
  bucketName: 'my-artifacts-bucket',
  model: 'global.anthropic.claude-opus-4-8',
  secretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:hdr-AbCdEf',
  assetS3Uri: 's3://asset-bucket/abc123.zip',
  userPoolId: 'ap-northeast-2_TESTPOOL',
  userPoolClientId: 'client-test',
  hostedUiDomain: 'pathfinder-test.auth.ap-northeast-2.amazoncognito.com',
  appUrl: 'https://example.cloudfront.net',
});

// 1) 안전 옵션·로그
assert.match(s, /set -euxo pipefail/, 'must be strict bash');
// 2) 에셋 다운로드
assert.match(s, /aws s3 cp s3:\/\/asset-bucket\/abc123\.zip/, 'must download asset');
// 3) 시크릿 부팅 조회 (하드코딩 금지 — 런타임 조회)
assert.match(s, /aws secretsmanager get-secret-value --secret-id arn:aws:secretsmanager:[^ ]+ /, 'must fetch secret at boot');
// 4) nginx 헤더 검증 (403)
assert.match(s, /\$http_x_origin_verify/, 'nginx must check X-Origin-Verify');
assert.match(s, /return 403/, 'nginx must 403 on mismatch');
// 4b) 이스케이핑 고정: nginx 런타임 변수는 백슬래시 정확히 1개만 남아야 부팅 시
//     bash가 heredoc에서 하나를 소비하고 nginx에 리터럴 $var를 남긴다.
//     단순 s.includes('\\$foo')는 백슬래시 2개짜리 회귀도 그대로 통과한다
//     ('\\\\$foo'가 '\\$foo'를 부분 문자열로 포함하기 때문) — 그래서 부정
//     lookbehind로 "바로 앞에 백슬래시가 없는 백슬래시"만 매치하도록 고정한다.
assert.match(s, /(?<!\\)\\\$http_x_origin_verify\b/, 'nginx var must keep exactly ONE backslash (not 0, not 2+) so bash leaves a literal $ for nginx');
assert.match(s, /(?<!\\)\\\$host\b/, 'nginx var \\$host must keep exactly ONE backslash');
assert.match(s, /(?<!\\)\\\$proxy_add_x_forwarded_for\b/, 'nginx var \\$proxy_add_x_forwarded_for must keep exactly ONE backslash');
// 4c) 대조: 시크릿 변수는 백슬래시 없이 남아야 부팅 시 bash가 실제 값으로 치환한다.
assert.ok(s.includes('"${SECRET}"'), 'secret var must be unescaped so bash expands it');
assert.ok(!s.includes('\\${SECRET}'), 'secret var must NOT be backslash-escaped');
// 5) nginx 라우팅
assert.match(s, /proxy_pass http:\/\/127\.0\.0\.1:8000\//, 'api -> backend');
assert.match(s, /proxy_pass http:\/\/127\.0\.0\.1:3000/, 'root -> frontend');
assert.match(s, /proxy_buffering off/, 'SSE: buffering off');
// 6) 프론트 빌드 env (same-origin API)
assert.match(s, /NEXT_PUBLIC_API_BASE_URL=\/api/, 'front build uses /api');
// 7) 백엔드 env
assert.match(s, /PATHFINDER_S3_BUCKET=my-artifacts-bucket/, 'backend bucket env');
assert.match(s, /ANTHROPIC_MODEL=global\.anthropic\.claude-opus-4-8/, 'backend model env');
assert.match(s, /PATHFINDER_S3_REGION=ap-northeast-2/, 'backend s3 region env');
// 8) systemd 유닛
assert.match(s, /pathfinder-backend\.service/, 'backend unit');
assert.match(s, /pathfinder-frontend\.service/, 'frontend unit');
assert.match(s, /uvicorn pathfinder\.app:app --host 127\.0\.0\.1 --port 8000/, 'uvicorn cmd');
assert.match(s, /next start -H 127\.0\.0\.1 -p 3000/, 'next start cmd');
// 9) 시크릿 하드코딩되지 않음 (실제 값은 부팅 시점에만 존재)
assert.ok(!s.includes('AbCdEf-value'), 'secret value never inlined');
// 10) 업로드 5MB 기능이 nginx 기본 1m 바디 제한에 막히지 않도록 여유치 설정.
assert.ok(s.includes('client_max_body_size 6m;'), 'nginx must allow up to 6m request bodies (5MB upload feature)');
// 11) AL2023 기본 nginx.conf의 server 블록 제거용 sed는 반드시 앵커링되어야 함.
//     비앵커 형태(`/server {/`)는 주석 처리된 TLS 예시 블록(`#    server {`)에도
//     매칭되어 range-delete가 EOF까지 이어지며 http{} 를 닫는 `}` 를 삭제,
//     nginx.conf 를 손상시킨다(nginx -t 실패 → 부트스트랩 전체 중단).
assert.ok(!s.includes("sed -i '/server {/"), 'must not use the unanchored sed that corrupts nginx.conf');
assert.ok(s.includes("sed -i '/^    server {/"), 'must use the anchored sed that only matches the real (uncommented) stock server block');

console.log('OK  user-data: all required elements present (incl. nginx-var vs secret-var escaping)');

// 9) 프로토타입 빌드: 동시 빌드 상한 + 빌드 에이전트 전용 CLAUDE_CONFIG_DIR env.
//    VM 시절의 PATHFINDER_VM_* 3종은 완전히 사라졌다(인프라에서 VM 계층 삭제).
assert.match(s, /Environment=PATHFINDER_PROTO_ROOT=\/opt\/pathfinder\/protos/, 'proto host root env');
assert.match(s, /Environment=PATHFINDER_PROTO_MAX_CONCURRENT=2/, 'proto build concurrency cap env');
// CLAUDE_CONFIG_DIR lives INSIDE the app tree, not in a user's home: app-owned
// data stays under one path for backup/cleanup/ownership, and nothing depends on
// where the service user's home happens to be.
assert.match(s, /Environment=PATHFINDER_PROTO_CONFIG_DIR=\/opt\/pathfinder\/proto-config/, 'proto build CLAUDE_CONFIG_DIR env must live under the app tree');
assert.ok(!s.includes('/home/ec2-user/pathfinder-proto-config'), 'CLAUDE_CONFIG_DIR must no longer point at a user home');
assert.match(s, /mkdir -p \/opt\/pathfinder\/protos \/opt\/pathfinder\/proto-config \/opt\/pathfinder\/workspaces/, 'app-owned data dirs must be created at boot');
assert.ok(!s.includes('PATHFINDER_VM_REGION'), 'PATHFINDER_VM_REGION must be gone');
assert.ok(!s.includes('PATHFINDER_VM_IMAGE_ID'), 'PATHFINDER_VM_IMAGE_ID must be gone');
assert.ok(!s.includes('PATHFINDER_VM_ROLE_ARN'), 'PATHFINDER_VM_ROLE_ARN must be gone');

console.log('OK  user-data: prototype build env vars rendered, VM env vars gone');

// 10) 서비스는 반드시 non-root로 돌아야 한다. Claude Code는 euid==0에서
//     bypassPermissions를 거부하고(6d21e1f 실측), `--version`은 root에서도
//     성공해 그 실패를 가린다 — 즉 부팅은 정상으로 보이고 첫 빌드 턴에서야
//     502로 드러난다. 이 어서션이 그 회귀를 부팅 전에 잡는 유일한 방어선이다.
const backendUnit = s.slice(s.indexOf('pathfinder-backend.service'), s.indexOf('pathfinder-frontend.service'));
assert.match(backendUnit, /^User=pathfinder$/m, 'backend service MUST run as the non-root pathfinder user (root breaks the build agent)');
assert.match(backendUnit, /^Group=pathfinder$/m, 'backend service must set its group too');
assert.match(s, /useradd --system --create-home --shell \/sbin\/nologin pathfinder/, 'service user must be created');
assert.match(s, /id -u pathfinder >\/dev\/null 2>&1 \|\|/, 'useradd must be idempotent for re-bootstrap');
assert.match(s, /dnf install -y [^\n]*shadow-utils/, 'shadow-utils provides useradd');
assert.match(s, /chown -R pathfinder:pathfinder \/opt\/pathfinder/, 'app tree must be handed to the service user (root unzipped it)');
// venv/npm must be built AS the service user, or the runtime user cannot use them.
assert.match(s, /runuser -u pathfinder -- python3\.11 -m venv/, 'venv must be created as the service user');
assert.match(s, /runuser -u pathfinder -- env NEXT_PUBLIC_API_BASE_URL=\/api HOME=\/opt\/pathfinder npm ci/, 'npm ci must run as the service user with a writable HOME');
// Both units need a writable HOME: the service user's real home is not used, and
// npx/npm and the bundled binary all want somewhere to cache.
assert.match(backendUnit, /^Environment=HOME=\/opt\/pathfinder$/m, 'backend unit needs a writable HOME');

console.log('OK  user-data: services run as non-root pathfinder user, app tree owned by it');

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
