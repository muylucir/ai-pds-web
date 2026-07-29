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
// 4d) 로그 파일 권한: pathfinder 서비스 유저(=bypassPermissions로 도는
//     프로토타입 빌드 에이전트가 쓰는 그 계정)가 부트스트랩 로그를 읽으면 안
//     된다 — 644(기본값)면 읽힌다.
assert.match(s, /chmod 600 \/var\/log\/pathfinder-bootstrap\.log/, 'bootstrap log must be root-only (600), not world/group-readable');
// 4e) xtrace(-x)는 두 시크릿 조회(Cognito client secret, X-Origin-Verify
//     header secret) 둘 다에서 꺼져 있어야 한다 — 켜져 있으면 명령과 그
//     결과 대입이 로그(사양상 pathfinder 유저가 읽는 그 로그)에 그대로
//     남는다. "set +x ... COGNITO_SECRET=... set -x"처럼 대입이 그 사이에
//     있는지 직접 확인한다(단순히 어딘가에 set +x가 있다는 것만으로는
//     부족하다 — 순서가 어긋나면 트레이스가 여전히 켜진 채로 대입이 실행된다).
for (const [name, pattern] of [
  ['COGNITO_SECRET', /COGNITO_SECRET=\$\(aws cognito-idp describe-user-pool-client/],
  ['SECRET (X-Origin-Verify)', /\nSECRET=\$\(aws secretsmanager get-secret-value/],
] as const) {
  const m = pattern.exec(s);
  assert.ok(m, `${name} assignment must exist`);
  const before = s.slice(0, m.index);
  const lastSetPlusX = before.lastIndexOf('set +x');
  const lastSetMinusX = before.lastIndexOf('set -x');
  assert.ok(lastSetPlusX !== -1 && lastSetPlusX > lastSetMinusX,
    `${name} assignment must be preceded by 'set +x' (with no intervening 'set -x') so xtrace never echoes it into the bootstrap log`);
  const after = s.slice(m.index);
  assert.match(after, /^\s*(?:.*\n){0,4}set -x/, `${name} assignment must be followed by 'set -x' to re-enable tracing afterward`);
}
// 5) nginx 라우팅 — /api/*는 FastAPI가 아니라 Next로 간다. Next의
//    app/api/[...path]/route.ts가 same-origin 프록시로서 쿠키를 Bearer로
//    번역해 서버사이드에서 FastAPI로 넘긴다; nginx가 /api/를 FastAPI로 직접
//    보내면 그 번역이 일어나지 않아 로그인/모든 API 호출이 깨진다(Bearer 없이
//    도달 -> 401, /api/auth/* 자체는 FastAPI에 없으므로 404).
assert.ok(!/proxy_pass http:\/\/127\.0\.0\.1:8000/.test(s),
  'FastAPI must not be reachable directly through nginx — every request goes through Next first');
{
  const nginxStart = s.indexOf('cat > /etc/nginx/conf.d/pathfinder.conf');
  const nginxEnd = s.indexOf('\nNGINX\n', nginxStart); // closing heredoc marker, not the opening "<<NGINX"
  const nginxBlock = s.slice(nginxStart, nginxEnd);
  // 정확히 하나의 location — /api/ 전용 location이 없다(있으면 FastAPI로 새는
  // 경로가 부활한다는 뜻).
  assert.strictEqual((nginxBlock.match(/location /g) ?? []).length, 1,
    'nginx must have exactly ONE location block — no separate /api/ block routing to FastAPI');
  assert.match(nginxBlock, /location \/ \{/, 'the single location must be the catch-all "/"');
  assert.match(nginxBlock, /proxy_pass http:\/\/127\.0\.0\.1:3000/, '/ -> frontend (which itself proxies /api to FastAPI)');
  // SSE 스트리밍 지시자는 이제 유일한 location에 있어야 한다 — browser ->
  // nginx -> Next -> FastAPI 경로 전체가 이 location을 지나간다.
  assert.match(nginxBlock, /proxy_buffering off/, 'SSE: buffering off must be present on the location that now carries /api traffic');
  assert.match(nginxBlock, /proxy_read_timeout 3600s/, 'SSE: long read timeout must be present on that same location');
  // 이 설계가 요구하는 방향으로 이 이음매를 못박는다: /api/auth/*(로그인·콜백·
  // /auth/me — 백엔드에는 이 라우트가 전혀 없다)는 반드시 프론트(Next)가
  // 받아야 한다. location이 하나뿐이고 그게 "/"이자 :3000으로 가므로, 이는
  // "/api/auth/도 그 하나의 location에 매칭된다"는 것을 직접 확인하는 것과
  // 같다 — nginx가 최長 접두어 매칭이므로 별도의 /api/ 블록이 없는 한 항상 참이다.
  const singleLocationCoversApiAuth = nginxBlock.includes('location / {')
    && !/location \/api\/? \{/.test(nginxBlock);
  assert.ok(singleLocationCoversApiAuth,
    '/api/auth/ specifically must be served by the frontend (:3000) — no /api/ location may exist to intercept it before it reaches Next');
}
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
assert.match(s, /Environment=PATHFINDER_PROTO_MAX_CONCURRENT=10/, 'proto build concurrency cap env');
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
  // 'COGNITO_CLIENT_ID=client-abc'만 찾으면 백엔드의
  // 'PATHFINDER_COGNITO_CLIENT_ID=client-abc' 줄도 부분 문자열로 매치되어
  // 프론트 줄이 삭제돼도 통과해버린다 — 'Environment=' 접두어까지 포함해
  // 프론트 systemd 줄만 매치하도록 고정한다.
  assert.ok(script.includes('COGNITO_HOSTED_UI_DOMAIN=pathfinder-x.auth.ap-northeast-2.amazoncognito.com'));
  assert.ok(script.includes('Environment=COGNITO_CLIENT_ID=client-abc'),
    'frontend unit must set COGNITO_CLIENT_ID (must not match the backend PATHFINDER_COGNITO_CLIENT_ID line)');
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

// 12) 프록시 응답 헤더 버퍼 — 로그인 성공 시 502가 나던 원인.
//
// 실측: Cognito 콜백이 access/id/refresh JWT 세 개를 Set-Cookie로 내보내는데
// 그 헤더 총량이 nginx 기본 버퍼를 넘어
// "upstream sent too big header while reading response header from upstream"
// 으로 502가 났다. `proxy_buffering off`(SSE 때문에 필수)일 때 nginx는 응답
// 헤더를 proxy_buffer_size 하나에만 담으므로, 그 값을 키워야 한다
// (proxy_buffers는 버퍼링이 꺼져 있으면 헤더에 쓰이지 않는다).
assert.match(s, /proxy_buffering off/,
  'SSE needs proxy_buffering off (immediate flush)');
const bufSize = s.match(/proxy_buffer_size\s+(\d+)([kKmM])/);
assert.ok(bufSize,
  'proxy_buffer_size must be set — with proxy_buffering off it is the ONLY buffer for response headers, and three JWT cookies overflow the 4k default (502)');
const kb = Number(bufSize![1]) * (/[mM]/.test(bufSize![2]) ? 1024 : 1);
assert.ok(kb >= 16,
  `proxy_buffer_size must be >= 16k to fit three JWT Set-Cookie headers, got ${bufSize![0]}`);
// proxy_buffer_size만 키우면 nginx가 설정을 거부한다(실측):
// "proxy_busy_buffers_size must be less than the size of all proxy_buffers
//  minus one buffer" — busy 기본값이 buffer_size의 2배로 따라 올라가며 기본
// proxy_buffers(8x4k)와의 제약을 깨기 때문이다. 세 값이 함께 정의되고 제약을
// 만족해야 nginx가 부팅한다(설정 거부 = 서비스 전체 down).
const bufs = s.match(/proxy_buffers\s+(\d+)\s+(\d+)([kKmM])/);
const busy = s.match(/proxy_busy_buffers_size\s+(\d+)([kKmM])/);
assert.ok(bufs, 'proxy_buffers must be set alongside proxy_buffer_size (nginx refuses the config otherwise)');
assert.ok(busy, 'proxy_busy_buffers_size must be set explicitly — its default follows proxy_buffer_size and breaks the constraint');
const toKb = (n: string, unit: string) => Number(n) * (/[mM]/.test(unit) ? 1024 : 1);
const bufsTotalKb = Number(bufs![1]) * toKb(bufs![2], bufs![3]);
const oneBufKb = toKb(bufs![2], bufs![3]);
const busyKb = toKb(busy![1], busy![2]);
assert.ok(busyKb < bufsTotalKb - oneBufKb,
  `nginx constraint violated: proxy_busy_buffers_size (${busyKb}k) must be < all proxy_buffers minus one (${bufsTotalKb - oneBufKb}k) — nginx would refuse to start`);
assert.ok(oneBufKb >= kb,
  `each proxy_buffer (${oneBufKb}k) should be at least proxy_buffer_size (${kb}k)`);
console.log('OK  user-data: proxy buffer trio sized for JWT Set-Cookie headers and nginx constraints');

// 13) 요청 헤더 버퍼 — 로그인 후 모든 요청이 JWT 쿠키 세 개를 싣는다.
// 응답 쪽(12)과 짝이다: 기본값 8k로는 Cookie 헤더가 넘쳐 400
// "Request Header Or Cookie Too Large"가 난다.
const cliBuf = s.match(/large_client_header_buffers\s+(\d+)\s+(\d+)([kKmM])/);
assert.ok(cliBuf,
  'large_client_header_buffers must be set — three JWT cookies overflow the 8k default on every authenticated request');
const cliKb = Number(cliBuf![2]) * (/[mM]/.test(cliBuf![3]) ? 1024 : 1);
assert.ok(cliKb >= 16,
  `large_client_header_buffers size must be >= 16k, got ${cliBuf![0]}`);
console.log('OK  user-data: large_client_header_buffers fits JWT cookies on requests');

// 14) Discovery 드라이버 env — 미주입 시 config dir이 호스트 유저의 ~/.claude로
// 떨어져 개인 skills/agents가 워크숍 결과에 섞인다(proto-config와 같은 이유).
assert.match(s, /PATHFINDER_DISCOVERY_CONFIG_DIR=/,
  'backend must get PATHFINDER_DISCOVERY_CONFIG_DIR — otherwise the host user\'s ~/.claude leaks into Discovery');
assert.match(s, /\/opt\/pathfinder\/discovery-config/,
  'discovery config dir must point at the shipped asset path');
// proto와 discovery의 config dir이 서로 다른 경로여야 한다 — 공유하면
// Discovery가 shadcn-design 스킬을 켠 채로 돈다(skills="all").
const protoCfg = s.match(/PATHFINDER_PROTO_CONFIG_DIR=([^\s\\]+)/);
const discCfg = s.match(/PATHFINDER_DISCOVERY_CONFIG_DIR=([^\s\\]+)/);
assert.ok(protoCfg && discCfg, 'both config dirs must be set');
assert.notStrictEqual(protoCfg![1], discCfg![1],
  'proto and discovery CLAUDE_CONFIG_DIRs must not be the same path');
console.log('OK  user-data: discovery config dir set and distinct from proto');
