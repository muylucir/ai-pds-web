import * as assert from 'node:assert';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { renderUserData } from '../lib/user-data';

const s = renderUserData({
  region: 'ap-northeast-2',
  bucketName: 'my-artifacts-bucket',
  model: 'global.anthropic.claude-opus-4-8',
  secretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:hdr-AbCdEf',
  repoUrl: 'https://github.com/example/repo.git',
  branch: 'main',
  userPoolId: 'ap-northeast-2_TESTPOOL',
  userPoolClientId: 'client-test',
  hostedUiDomain: 'aipds-test.auth.ap-northeast-2.amazoncognito.com',
});

// 1) Safety options and logging
assert.match(s, /set -euxo pipefail/, 'must be strict bash');
// 2) Code deployment: clone the repo + align to the remote tip of the deploy branch.
//    No asset zip and no commit SHA pinning — see lib/deploy-source.ts for why.
assert.match(s, /git clone https:\/\/github\.com\/example\/repo\.git \/opt\/aipds/,
  'must clone the repo into the app tree');
assert.match(s, /git -C \/opt\/aipds checkout -f -B main origin\/main/,
  'must force the local branch onto origin/<branch> — the same line has to work on a '
  + 'fresh clone and on a cloud-init re-run');
assert.ok(!s.includes('checkout --detach'),
  'the deployment is no longer pinned to a commit; a detached checkout would make '
  + 'aipds-update unable to move the tree forward');
// The boot commit must be recorded in the log — the SHA is not in the deployment, so otherwise
// the only way to answer "what is running" is to ask the instance.
assert.match(s, /git -C \/opt\/aipds --no-pager log -1 --format='booted commit: %H %s'/,
  'the commit the instance booted on must be recorded in the bootstrap log');
assert.ok(!/aws s3 cp .*\.zip/.test(s),
  'the asset zip download must be gone (the tree now comes from git, which carries only tracked files)');
assert.ok(!s.includes('unzip -o'),
  'nothing is unzipped any more');
assert.match(s, /dnf install -y [^\n]*\bgit\b/, 'git must be installed — AL2023 does not ship it');
// After the clone the tree is handed to the service user, so on re-bootstrap git running as root
// is refused with "dubious ownership". Under set -e that aborts the boot.
assert.match(s, /git config --system --add safe\.directory \/opt\/aipds/,
  'safe.directory must be configured before git runs on the service-user-owned tree');
// A submodule is a separate repo with its own git directory — git checks ownership per repo.
assert.match(s, /git config --system --add safe\.directory \/opt\/aipds\/steering-files/,
  'the steering-files submodule needs its own safe.directory entry — it is a separate '
  + 'repository, so registering only the app tree still fails on a cloud-init re-run');
// The AI-PLC ruleset is a submodule of the upstream repo, and clone does not fetch submodule contents.
// Leave it out and the boot succeeds with no rules — the symptom only shows on the first Discovery turn.
assert.match(s, /git -C \/opt\/aipds submodule update --init --recursive/,
  'the ruleset submodule must be populated at boot — a clone leaves steering-files/ empty '
  + 'and the failure only surfaces on the first Discovery turn');
// With --remote, every boot pulls the upstream tip and the instance drifts from the rules the repo pins.
// Anchoring the start with `^\s*git` is so comments (`# ... --remote ...`) are not counted.
assert.ok(!/^\s*git[^\n]*submodule update[^\n]*--remote/m.test(s),
  'boot must use the submodule commit the repo pins, not --remote — otherwise the rules '
  + 'running on the instance are not the rules the repo records');
// Idempotent: on a cloud-init re-run, trying to clone an already-cloned tree fails.
assert.match(s, /if \[ -d \/opt\/aipds\/\.git \]; then/,
  're-bootstrap must fetch instead of re-cloning');
// 2a) **The EC2 user-data 16KB limit.** It was exceeded once and the deploy failed with InvalidRequest
//     (18,158 bytes — 12KB of it was comments, and Hangul is 3 bytes/char). CFN rejects this only at
//     deploy time, and that failure shows up as an instance **replacement** failure, so the stack
//     rolls back (taking IAM changes in the same stack back with it). Stop it here.
{
  const bytes = Buffer.byteLength(s, 'utf8');
  assert.ok(bytes <= 16384,
    `user-data is ${bytes} bytes — EC2 rejects anything over 16384. Move long blocks to `
    + 'infra/scripts/ (installed from the clone) and put the "why" in TS comments '
    + 'outside the template literal, where it costs zero bytes.');
  // Without headroom the next single line pushes it over the limit again — keep at least half free.
  assert.ok(bytes <= 12288,
    `user-data is ${bytes} bytes: under the hard limit but with less than 4KB of room. `
    + 'Trim before adding more.');
  console.log(`OK  user-data: ${bytes} bytes (limit 16384, headroom ${16384 - bytes})`);
}
// 2a') The sandbox is installed and enabled **before** the services start (scripts/aipds-harden boot). If it comes after, the
//      backend starts first without the wrapper and launches agents and prototypes with the instance role. Without region, boot
//      cannot read SSM and leaves isolation off.
{
  const boot = s.indexOf('AWS_REGION=ap-northeast-2 /opt/aipds/infra/scripts/aipds-harden boot');
  assert.ok(boot >= 0, 'user-data must run aipds-harden boot with the region');
  assert.ok(!s.includes('aipds-harden install'),
    'install alone leaves the launcher and the IMDS block off on a new instance');
  assert.ok(boot < s.indexOf('systemctl enable --now nginx aipds-backend aipds-frontend'),
    'aipds-harden boot must run before the backend starts');
}
// 2b) Code update path. There is no SHA in the deployment, so cdk deploy neither replaces the instance nor
//     updates the code — this script is the **only** means of updating. If it is missing or
//     broken the deploy still succeeds, and that only comes to light when an update is attempted.
//
//     The body lives in a repo file (infra/scripts/aipds-update), not in user-data:
//     it does not spend the 16KB budget, and editing the script needs no instance replacement. So
//     this section asserts each side separately — user-data only installs and configures; the rest is that file.
{
  assert.match(s, /install -m 755 \/opt\/aipds\/infra\/scripts\/aipds-update \/usr\/local\/bin\/aipds-update/,
    'user-data must install aipds-update from the clone — with a branch (not a SHA) '
    + 'deployment, cdk deploy no longer updates code and this is the only path that does');
  assert.ok(!s.includes("<<'UPDATE'"),
    'the update script must not be inlined back into user-data — that is what blew the '
    + '16KB limit, and editing it would force an instance replacement');
  // The script reads the path, user and branch from this file. Hardcoding them as literals
  // would drift silently when DEPLOY_BRANCH changes.
  assert.match(s, /cat > \/etc\/aipds-deploy\.env <<ENV\nAPP=\/opt\/aipds\nSVC=aipds\nBRANCH=main\nENV/,
    'user-data must write the deploy env the script reads (APP/SVC/BRANCH)');

  const scriptPath = require('node:path').join(__dirname, '..', 'scripts', 'aipds-update');
  const fs = require('node:fs') as typeof import('node:fs');
  assert.ok(fs.existsSync(scriptPath), 'infra/scripts/aipds-update must exist');
  // Executable bit: user-data installs it with install -m 755, but there is also the path of running it straight from the repo.
  assert.ok((fs.statSync(scriptPath).mode & 0o111) !== 0,
    'infra/scripts/aipds-update must be executable in the repo');
  const update: string = fs.readFileSync(scriptPath, 'utf8');

  assert.match(update, /\. "\$ENV_FILE"/,
    'the script must source the deploy env instead of hardcoding APP/SVC/BRANCH');
  // With no argument, the remote tip of the deploy branch; with one, of that test branch (developer-only — see the script header).
  assert.match(update, /TARGET=\$\{1:-\$BRANCH\}/,
    'aipds-update must default to the deploy branch when no branch is given');
  assert.match(update, /checkout -f -B "\$TARGET" "origin\/\$TARGET"/,
    'aipds-update must move the tree onto the remote tip of the deploy (or test) branch');
  // checkout moves only the gitlink, not the submodule contents — leave this out and rule updates
  // silently do not take effect (in a branch deploy this script is the only code update path).
  assert.match(update, /submodule update --init --recursive/,
    'aipds-update must populate the ruleset submodule — checkout moves the gitlink but '
    + 'not its contents, so a rules update would silently not take effect');
  // It must be **above** the early exit: an instance that booted with a failed submodule init has an
  // unchanged commit, so it takes the early exit, and then the only fix is replacing the instance.
  assert.ok(update.indexOf('submodule update --init') < update.indexOf('BEFORE" = "$AFTER'),
    'the submodule update must run before the up-to-date early exit, so that an instance '
    + 'which booted with an empty steering-files/ can heal itself without a redeploy');
  // Build without this env and the browser calls localhost:8000 — the screen loads and every
  // API call dies. This one line is the main reason the manual procedure was moved into a script.
  assert.match(update, /env NEXT_PUBLIC_API_BASE_URL=\/api HOME="\$APP" npm run build/,
    'the frontend rebuild must carry NEXT_PUBLIC_API_BASE_URL=/api — without it the '
    + 'bundle points the browser at localhost:8000 and every API call dies silently');
  assert.match(update, /systemctl restart aipds-backend/, 'must be able to restart the backend');
  assert.match(update, /systemctl restart aipds-frontend/, 'must be able to restart the frontend');
  // The tree is owned by the service user. Fetching as root mixes root-owned new objects in, and
  // the next update is blocked by permissions — that failure only shows when updating.
  assert.match(update, /runuser -u "\$SVC" -- git -C "\$APP"/,
    'git must run as the service user that owns the tree, not as root');
  // Do not restart when there is nothing to update: restarting the backend cuts in-flight Discovery
  // turns and build sessions.
  assert.match(update, /if \[ "\$BEFORE" = "\$AFTER" \]; then/,
    'an up-to-date instance must be left alone — a needless backend restart cuts '
    + 'in-flight Discovery turns and build sessions');
  // Runtime data (protos/, workspaces/, */sessions/) is untracked, so reset --hard does not
  // delete it, but git clean or rm -rf would.
  assert.ok(!/git clean/.test(update),
    'aipds-update must not git clean — protos/, workspaces/ and session state are untracked');
  assert.ok(!/rm -rf/.test(update),
    'aipds-update must not delete the tree — it updates it in place');
  console.log('OK  user-data: aipds-update ships (branch deploys have no other code-update path)');
}
// 3) Secret lookup at boot (no hardcoding — runtime lookup)
assert.match(s, /aws secretsmanager get-secret-value --secret-id arn:aws:secretsmanager:[^ ]+ /, 'must fetch secret at boot');
// 4) nginx header check (403)
assert.match(s, /\$http_x_origin_verify/, 'nginx must check X-Origin-Verify');
assert.match(s, /return 403/, 'nginx must 403 on mismatch');
// 4b) Pin the escaping: nginx runtime variables must keep exactly 1 backslash so that at boot
//     bash consumes one in the heredoc and leaves a literal $var for nginx.
//     A plain s.includes('\\$foo') also passes a 2-backslash regression
//     (because '\\\\$foo' contains '\\$foo' as a substring) — so a negative
//     lookbehind pins it to match only "a backslash not immediately preceded by a backslash".
assert.match(s, /(?<!\\)\\\$http_x_origin_verify\b/, 'nginx var must keep exactly ONE backslash (not 0, not 2+) so bash leaves a literal $ for nginx');
assert.match(s, /(?<!\\)\\\$host\b/, 'nginx var \\$host must keep exactly ONE backslash');
assert.match(s, /(?<!\\)\\\$proxy_add_x_forwarded_for\b/, 'nginx var \\$proxy_add_x_forwarded_for must keep exactly ONE backslash');
// 4c) Contrast: secret variables must stay without a backslash so bash substitutes the real value at boot.
assert.ok(s.includes('"${SECRET}"'), 'secret var must be unescaped so bash expands it');
assert.ok(!s.includes('\\${SECRET}'), 'secret var must NOT be backslash-escaped');
// 4d) Log file permissions: the aipds service user (= the account used by the prototype
//     build agent running with bypassPermissions) must not be able to read the bootstrap
//     log — at 644 (the default) it can.
assert.match(s, /chmod 600 \/var\/log\/aipds-bootstrap\.log/, 'bootstrap log must be root-only (600), not world/group-readable');
// 4e) xtrace (-x) must be off around both secret lookups (Cognito client secret, X-Origin-Verify
//     header secret) — if it is on, the command and its result assignment end up verbatim
//     in the log (the very log the aipds user reads, by design). Check directly that the
//     assignment sits in between, as in "set +x ... COGNITO_SECRET=... set -x" (merely having
//     set +x somewhere is not enough — if the order is off, the assignment still runs with
//     tracing on).
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
// 5) nginx routing — /api/* goes to Next, not FastAPI. Next's
//    app/api/[...path]/route.ts, as the same-origin proxy, translates the cookie into a
//    Bearer and passes it to FastAPI server-side; if nginx sent /api/ directly to FastAPI,
//    that translation would not happen and login and every API call would break (arrives
//    without Bearer -> 401, and /api/auth/* itself does not exist in FastAPI, so 404).
assert.ok(!/proxy_pass http:\/\/127\.0\.0\.1:8000/.test(s),
  'FastAPI must not be reachable directly through nginx — every request goes through Next first');
{
  const nginxStart = s.indexOf('cat > /etc/nginx/conf.d/aipds.conf');
  const nginxEnd = s.indexOf('\nNGINX\n', nginxStart); // closing heredoc marker, not the opening "<<NGINX"
  const nginxBlock = s.slice(nginxStart, nginxEnd);
  // Exactly one location — no dedicated /api/ location (one would mean the path leaking to
  // FastAPI has come back).
  assert.strictEqual((nginxBlock.match(/location /g) ?? []).length, 1,
    'nginx must have exactly ONE location block — no separate /api/ block routing to FastAPI');
  assert.match(nginxBlock, /location \/ \{/, 'the single location must be the catch-all "/"');
  assert.match(nginxBlock, /proxy_pass http:\/\/127\.0\.0\.1:3000/, '/ -> frontend (which itself proxies /api to FastAPI)');
  // The SSE streaming directives must now be on the only location — the whole browser ->
  // nginx -> Next -> FastAPI path passes through this location.
  assert.match(nginxBlock, /proxy_buffering off/, 'SSE: buffering off must be present on the location that now carries /api traffic');
  assert.match(nginxBlock, /proxy_read_timeout 3600s/, 'SSE: long read timeout must be present on that same location');
  // Only nginx can compress — the Next /api proxy sends the body out decompressed, and
  // CloudFront (CACHING_DISABLED) does not compress. CloudFront adds Via, so without
  // gzip_proxied, gzip on does nothing.
  assert.match(nginxBlock, /gzip on;/, 'nginx must gzip — nothing else in the chain compresses /api responses');
  assert.match(nginxBlock, /gzip_proxied any;/, 'gzip_proxied any: CloudFront requests carry Via, so the default (off) compresses nothing');
  const gzipTypes = nginxBlock.match(/gzip_types ([^;]+);/);
  assert.ok(gzipTypes, 'gzip_types must be set');
  assert.ok(!gzipTypes[1].includes('text/event-stream'),
    'SSE must never be gzipped — compression buffers events and stalls the stream');
  // Pin this seam in the direction this design requires: /api/auth/* (login, callback,
  // /auth/me — the backend has none of these routes) must be received by the frontend (Next).
  // There is only one location and it is "/" going to :3000, so this is the same as
  // directly checking that "/api/auth/ also matches that one location" — nginx uses
  // longest-prefix matching, so it always holds as long as there is no separate /api/ block.
  const singleLocationCoversApiAuth = nginxBlock.includes('location / {')
    && !/location \/api\/? \{/.test(nginxBlock);
  assert.ok(singleLocationCoversApiAuth,
    '/api/auth/ specifically must be served by the frontend (:3000) — no /api/ location may exist to intercept it before it reaches Next');
}
// 6) Frontend build env (same-origin API)
assert.match(s, /NEXT_PUBLIC_API_BASE_URL=\/api/, 'front build uses /api');
// 7) Backend env
assert.match(s, /AIPDS_S3_BUCKET=my-artifacts-bucket/, 'backend bucket env');
assert.match(s, /ANTHROPIC_MODEL=global\.anthropic\.claude-opus-4-8/, 'backend model env');
assert.match(s, /AIPDS_S3_REGION=ap-northeast-2/, 'backend s3 region env');
// 8) systemd units
assert.match(s, /aipds-backend\.service/, 'backend unit');
assert.match(s, /aipds-frontend\.service/, 'frontend unit');
assert.match(s, /uvicorn aipds\.app:app --host 127\.0\.0\.1 --port 8000/, 'uvicorn cmd');
assert.match(s, /next start -H 127\.0\.0\.1 -p 3000/, 'next start cmd');
// 9) Secret not hardcoded (the real value exists only at boot time)
assert.ok(!s.includes('AbCdEf-value'), 'secret value never inlined');
// 10) Set headroom so attachment uploads (10MB) are not blocked by nginx's default 1m body limit.
//     Must match the value in aipds-update, which brings running instances in line.
assert.ok(s.includes('client_max_body_size 11m;'), 'nginx must allow up to 11m request bodies (10MB upload feature)');
{
  const update = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'aipds-update'), 'utf8');
  assert.ok(update.includes('NGINX_BODY_LIMIT=11m'), 'aipds-update must keep live instances at the same nginx body limit as user-data');
}
// 11) The sed that removes the server block from AL2023's stock nginx.conf must be anchored.
//     The unanchored form (`/server {/`) also matches the commented-out TLS example block (`#    server {`),
//     so the range-delete runs to EOF and deletes the `}` that closes http{},
//     corrupting nginx.conf (nginx -t fails → the whole bootstrap aborts).
assert.ok(!s.includes("sed -i '/server {/"), 'must not use the unanchored sed that corrupts nginx.conf');
assert.ok(s.includes("sed -i '/^    server {/"), 'must use the anchored sed that only matches the real (uncommented) stock server block');

console.log('OK  user-data: all required elements present (incl. nginx-var vs secret-var escaping)');

// 9) Prototype build: concurrent build cap + CLAUDE_CONFIG_DIR env dedicated to the build agent.
assert.match(s, /Environment=AIPDS_PROTO_ROOT=\/opt\/aipds\/protos/, 'proto host root env');
assert.match(s, /Environment=AIPDS_PROTO_MAX_CONCURRENT=10/, 'proto build concurrency cap env');
// CLAUDE_CONFIG_DIR lives INSIDE the app tree, not in a user's home: app-owned
// data stays under one path for backup/cleanup/ownership, and nothing depends on
// where the service user's home happens to be.
assert.match(s, /Environment=AIPDS_PROTO_CONFIG_DIR=\/opt\/aipds\/proto-config/, 'proto build CLAUDE_CONFIG_DIR env must live under the app tree');
assert.doesNotMatch(s, /AIPDS_PROTO_CONFIG_DIR=\/home\//, 'CLAUDE_CONFIG_DIR must not point at a user home');
assert.match(s, /mkdir -p \/opt\/aipds\/protos \/opt\/aipds\/proto-config \/opt\/aipds\/workspaces/, 'app-owned data dirs must be created at boot');

// Secure switch for the prototype access cookie (_cookie_secure in routes/proto_public.py).
// The default is off, so if this line goes missing the deployment silently issues non-Secure cookies —
// there is no visible symptom on screen, so only a test can catch it.
assert.match(s, /Environment=AIPDS_COOKIE_SECURE=true/,
  'AIPDS_COOKIE_SECURE=true must be set — the backend omits Secure on the prototype access cookie without it');

// Two context settings (backend/aipds/cli_settings.py). Same failure shape as COOKIE_SECURE:
// both default to off, so if these lines go missing the deployment silently falls back to the
// old behavior — the state where later-stage documents come from a compaction summary rather
// than the evidence, and the only visible symptom is "the documents are a bit thin".
const compactWindow = s.match(/Environment=AIPDS_AUTO_COMPACT_WINDOW=(\d+)/);
assert.ok(compactWindow,
  'AIPDS_AUTO_COMPACT_WINDOW must be set — without it the CLI compacts at its own default and later Discovery stages write from a summary');
const windowTokens = Number(compactWindow![1]);
// cli_settings._WINDOW_MIN.._WINDOW_MAX. The backend warns and ignores values outside it, so
// whoever set it believes it is on, but it actually runs at the CLI default.
assert.ok(windowTokens >= 100_000 && windowTokens <= 1_000_000,
  `AIPDS_AUTO_COMPACT_WINDOW=${windowTokens} is outside the CLI's accepted range 100000..1000000 — the backend would warn and ignore it`);
assert.match(s, /Environment=AIPDS_LONG_CONTEXT=true/,
  'AIPDS_LONG_CONTEXT=true must be set — on Bedrock the Opus profile needs the [1m] suffix for a 1M window');
// **Turn both on together.** Giving a window over 200k without 1M hits the model's context limit
// before compaction kicks in — the failure then is a turn error, not compaction.
if (windowTokens > 200_000) {
  assert.match(s, /Environment=AIPDS_LONG_CONTEXT=true/,
    `AIPDS_AUTO_COMPACT_WINDOW=${windowTokens} exceeds the 200k default window — AIPDS_LONG_CONTEXT=true is required alongside it`);
}

console.log('OK  user-data: prototype build env vars, cookie-secure switch, and compaction/long-context env rendered');

// 10) The services must run as non-root. Claude Code refuses bypassPermissions at
//     euid==0 (observed in 6d21e1f), and `--version` succeeds even as root, masking
//     that failure — so the boot looks normal and it only surfaces as a 502 on the first
//     build turn. This assertion is the only line of defense that catches that regression before boot.
const backendUnit = s.slice(s.indexOf('aipds-backend.service'), s.indexOf('aipds-frontend.service'));
assert.match(backendUnit, /^User=aipds$/m, 'backend service MUST run as the non-root aipds user (root breaks the build agent)');
assert.match(backendUnit, /^Group=aipds$/m, 'backend service must set its group too');
assert.match(s, /useradd --system --create-home --shell \/sbin\/nologin aipds/, 'service user must be created');
assert.match(s, /id -u aipds >\/dev\/null 2>&1 \|\|/, 'useradd must be idempotent for re-bootstrap');
assert.match(s, /dnf install -y [^\n]*shadow-utils/, 'shadow-utils provides useradd');
assert.match(s, /chown -R aipds:aipds \/opt\/aipds/, 'app tree must be handed to the service user (root unzipped it)');
// venv/npm must be built AS the service user, or the runtime user cannot use them.
assert.match(s, /runuser -u aipds -- python3\.11 -m venv/, 'venv must be created as the service user');
assert.match(s, /runuser -u aipds -- env NEXT_PUBLIC_API_BASE_URL=\/api HOME=\/opt\/aipds npm ci/, 'npm ci must run as the service user with a writable HOME');
// Both units need a writable HOME: the service user's real home is not used, and
// npx/npm and the bundled binary all want somewhere to cache.
assert.match(backendUnit, /^Environment=HOME=\/opt\/aipds$/m, 'backend unit needs a writable HOME');

console.log('OK  user-data: services run as non-root aipds user, app tree owned by it');

// --- Auth wiring (Cognito) ---
{
  const script = renderUserData({
    region: 'ap-northeast-2',
    bucketName: 'bucket-x',
    model: 'model-x',
    secretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:abc',
    repoUrl: 'https://github.com/example/repo.git',
    branch: 'main',
    userPoolId: 'ap-northeast-2_POOL',
    userPoolClientId: 'client-abc',
    hostedUiDomain: 'aipds-x.auth.ap-northeast-2.amazoncognito.com',
  });

  // Backend: auth is on only with these two values (without them, bypass = public with no auth).
  assert.ok(script.includes('AIPDS_COGNITO_USER_POOL_ID=ap-northeast-2_POOL'),
    'backend must receive the user pool id — without it auth is bypassed');
  assert.ok(script.includes('AIPDS_COGNITO_CLIENT_ID=client-abc'),
    'backend must receive the client id');

  // Frontend: Hosted UI domain and client. The app URL is not here — HostingStack's distribution
  // reaches the instance through a VPC origin, so user-data referencing the distribution would be a
  // cycle; aipds-harden applies it from SSM.
  // Searching only for 'COGNITO_CLIENT_ID=client-abc' would also match the backend's
  // 'AIPDS_COGNITO_CLIENT_ID=client-abc' line as a substring, so it would pass even if
  // the frontend line were deleted — include the 'Environment=' prefix to pin it to
  // the frontend systemd line only.
  assert.ok(script.includes('COGNITO_HOSTED_UI_DOMAIN=aipds-x.auth.ap-northeast-2.amazoncognito.com'));
  assert.ok(script.includes('Environment=COGNITO_CLIENT_ID=client-abc'),
    'frontend unit must set COGNITO_CLIENT_ID (must not match the backend AIPDS_COGNITO_CLIENT_ID line)');
  assert.ok(!script.includes('Environment=APP_BASE_URL='),
    'user-data must not carry the app URL (it would reference the distribution — a cycle)');

  // The client secret is fetched from Cognito at boot — it is not left in plaintext in the template.
  assert.ok(script.includes('describe-user-pool-client'),
    'client secret must be fetched at boot, not baked into the template');
  assert.ok(!script.includes('COGNITO_CLIENT_SECRET=secret'),
    'a literal client secret must never appear in user-data');

  // With the NEXT_PUBLIC_ prefix, the secret ships in the client bundle.
  assert.ok(!script.includes('NEXT_PUBLIC_COGNITO'),
    'Cognito env must never be NEXT_PUBLIC_ (it would be inlined into the browser bundle)');

  console.log('OK  user-data: cognito env for backend + frontend, secret fetched at boot');
}

// 12) Proxy response header buffer — the cause of the 502 on successful login.
//
// Observed: the Cognito callback sends three JWTs (access/id/refresh) as Set-Cookie, and
// the total header size exceeded nginx's default buffer, giving a 502 with
// "upstream sent too big header while reading response header from upstream".
// With `proxy_buffering off` (required for SSE), nginx holds the response
// headers in a single proxy_buffer_size buffer, so that value has to grow
// (proxy_buffers is not used for headers when buffering is off).
assert.match(s, /proxy_buffering off/,
  'SSE needs proxy_buffering off (immediate flush)');
const bufSize = s.match(/proxy_buffer_size\s+(\d+)([kKmM])/);
assert.ok(bufSize,
  'proxy_buffer_size must be set — with proxy_buffering off it is the ONLY buffer for response headers, and three JWT cookies overflow the 4k default (502)');
const kb = Number(bufSize![1]) * (/[mM]/.test(bufSize![2]) ? 1024 : 1);
assert.ok(kb >= 16,
  `proxy_buffer_size must be >= 16k to fit three JWT Set-Cookie headers, got ${bufSize![0]}`);
// Growing only proxy_buffer_size makes nginx reject the config (observed):
// "proxy_busy_buffers_size must be less than the size of all proxy_buffers
//  minus one buffer" — because the busy default rises along with it to 2x buffer_size and
// breaks the constraint with the default proxy_buffers (8x4k). nginx boots only when the three
// values are defined together and satisfy the constraint (config rejected = whole service down).
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

// 13) Request header buffer — after login, every request carries the three JWT cookies.
// Pairs with the response side (12): at the 8k default the Cookie header overflows with a 400
// "Request Header Or Cookie Too Large".
const cliBuf = s.match(/large_client_header_buffers\s+(\d+)\s+(\d+)([kKmM])/);
assert.ok(cliBuf,
  'large_client_header_buffers must be set — three JWT cookies overflow the 8k default on every authenticated request');
const cliKb = Number(cliBuf![2]) * (/[mM]/.test(cliBuf![3]) ? 1024 : 1);
assert.ok(cliKb >= 16,
  `large_client_header_buffers size must be >= 16k, got ${cliBuf![0]}`);
console.log('OK  user-data: large_client_header_buffers fits JWT cookies on requests');

// 14) Discovery driver env — if not injected, the config dir falls back to the host user's ~/.claude
// and personal skills/agents get mixed into workshop results (same reason as proto-config).
assert.match(s, /AIPDS_DISCOVERY_CONFIG_DIR=/,
  'backend must get AIPDS_DISCOVERY_CONFIG_DIR — otherwise the host user\'s ~/.claude leaks into Discovery');
assert.match(s, /\/opt\/aipds\/discovery-config/,
  'discovery config dir must point at the shipped asset path');
// The proto and discovery config dirs must be different paths — if shared,
// Discovery runs with the shadcn-design skill enabled (skills="all").
const protoCfg = s.match(/AIPDS_PROTO_CONFIG_DIR=([^\s\\]+)/);
const discCfg = s.match(/AIPDS_DISCOVERY_CONFIG_DIR=([^\s\\]+)/);
assert.ok(protoCfg && discCfg, 'both config dirs must be set');
assert.notStrictEqual(protoCfg![1], discCfg![1],
  'proto and discovery CLAUDE_CONFIG_DIRs must not be the same path');
console.log('OK  user-data: discovery config dir set and distinct from proto');
