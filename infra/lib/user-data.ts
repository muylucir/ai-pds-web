export interface UserDataOptions {
  region: string;
  bucketName: string;
  /**
   * Bedrock inference profile ID passed down as ANTHROPIC_MODEL. Model calls go through Amazon Bedrock,
   * not the Anthropic API — `CLAUDE_CODE_USE_BEDROCK=1` is injected by the backend when it launches
   * Claude Code (aipds/agent/claude_driver.py, aipds/proto/builder.py).
   */
  model: string;
  secretArn: string;
  /** HTTPS clone URL of the public repo. */
  repoUrl: string;
  /**
   * Deploy target branch. At boot, the tree is aligned to this branch's **remote tip commit**.
   *
   * The consequence of this being a branch rather than a commit SHA: user-data is identical regardless
   * of code changes, so `cdk deploy` does not replace the instance. Code updates are done by
   * `aipds-update`, installed below. The reasoning is in lib/deploy-source.ts.
   */
  branch: string;
  // Auth. If these values are empty, the backend runs with auth bypassed and the deployment is
  // public without auth — the hosting stack always fills them.
  userPoolId: string;
  userPoolClientId: string;
  hostedUiDomain: string;
  appUrl: string;
}

// EC2 bootstrap script. Pure string generation (no side effects) — unit-testable.
// At boot: install packages → create the service user → clone the repo → backend venv/install →
// frontend build → secret lookup → nginx conf → start systemd. nginx returns 403 on a header mismatch.
export function renderUserData(opts: UserDataOptions): string {
  const { region, bucketName, model, secretArn, repoUrl, branch,
          userPoolId, userPoolClientId, hostedUiDomain, appUrl } = opts;
  const APP = '/opt/aipds';
  // Dedicated non-root service user. It is required, not a convenience: the Claude Code binary the
  // prototype build agent launches refuses bypassPermissions at euid==0
  // (observed in 6d21e1f — `--version` succeeds even as root and masks this failure, so
  // the boot looks normal and it only surfaces as a 502 on the first build turn). That is why the MicroVM image
  // used a non-root 'harness' user, and now that builds have been absorbed into the backend
  // process, the backend itself must be non-root.
  const SVC = 'aipds';
  // ---------------------------------------------------------------------------
  // Comments inside the template string below **spend the EC2 user-data 16KB limit.** It was exceeded once
  // and the deploy failed with InvalidRequest (18,158 bytes, 12KB of it comments, with Hangul at
  // 3 bytes/char). So the "why" is written here (TS comments = 0 bytes), and inside the template only
  // the one line someone reading the script on the instance needs to know **so as not to break it**
  // is left. One long procedure (aipds-update) lives entirely in a repo file
  // (infra/scripts/aipds-update — that file's comments do not count against the budget).
  //
  // Why the bootstrap log is locked to 600: at 644 the service user can read it, and that
  // account is the one that runs the prototype build agent with bypassPermissions. The two `set +x`
  // below hide the secret assignments, but rather than rely on that alone, the file itself is locked.
  //
  // Why git clone instead of an S3 asset zip, and why a branch instead of a commit SHA: lib/deploy-source.ts.
  // In short, (1) the asset carried even gitignored files, needed a correction list, and what got deployed
  // was the working tree rather than a commit; (2) the deployer does not have to worry about "did I push
  // this commit". The price is that cdk deploy cannot update code, and aipds-update fills that
  // gap.
  //
  // `checkout -f -B` must produce the same result on the first clone and on a cloud-init re-run. -f is
  // needed for the re-run — if even one tracked file is modified, a checkout without -f
  // is refused, and under set -e that refusal aborts the boot (symptom: 502). Untracked files
  // (protos/, workspaces/, session state) are not deleted even with -f.
  //
  // Why `safe.directory` is added up front: the tree is handed to SVC below, so on a re-run git
  // running as root is refused with "dubious ownership", which also aborts the boot.
  //
  // Why the two CLAUDE_CONFIG_DIRs (proto-config, discovery-config) live inside the app tree and at **different
  // paths**: app-owned data is gathered under one path, and if shared, skills="all" makes
  // Discovery run with the shadcn-design skill for prototype builds enabled.
  //
  // ⚠️ This file is a TS template literal — **do not use backticks, even in comments.** A single backtick
  // closes the literal and the whole user-data becomes a parse error (this applies to comments inside the template too).
  //
  // AIPDS_COOKIE_SECURE: the Secure switch for the prototype access cookie
  // (_cookie_secure in routes/proto_public.py). Leave it out and the default (off) omits Secure;
  // because of CloudFront it appears to work normally, but the cookie is left in a state where it can also
  // be sent over plaintext HTTP — there is no visible symptom to catch by eye, so
  // user-data.assert.ts asserts this line exists (it was left out once).
  // It is not turned on in local development (on localhost the browser does not store Secure cookies,
  // so the preview does not open).
  //
  // AIPDS_AUTO_COMPACT_WINDOW / AIPDS_LONG_CONTEXT: turn both on together
  // (cli_settings.py). Raising only the window without turning on 1M hits the model context
  // limit before compaction. Why turn them on: observed (2026-08-13), a build session was summarized from 264,040 → 53,375
  // tokens, and after the summary the later-stage documents came from the summary rather than the evidence,
  // getting thinner the further along. Korean uses 1.66x the tokens for the same conversation and reaches that point 40%
  // earlier. 750000 is about 4x the trigger point while leaving a margin under 1M.
  // The price is per-turn cost (with late compaction the full history is resent every turn — cache reads
  // are 0.1x, but not 0) — if workshop cost is a concern, lower this value first. Why Bedrock
  // needs the 1M suffix is in the docstring of cli_settings.cli_model_id.
  //
  // Frontend unit: HOME must be writable for npx to write its cache (the root home cannot be used).
  // Do not prefix COGNITO_* with NEXT_PUBLIC_ — it would be inlined into the client bundle
  // and the secret would ship to the browser. Expanding CLIENT_SECRET unquoted is
  // safe (Cognito secrets are [A-Za-z0-9_+]{24,64} with no shell metacharacters).
  //
  // nginx: after login, requests carry three JWT cookies (access/id/refresh) — at the default buffers
  // (4k/8k) the Cookie header overflows with a 400, so both request and response buffers are raised.
  // Not having a dedicated `/api/` location is also intentional: /api/* is where Next's
  // app/api/[...path]/route.ts hands off to FastAPI server-side, translating the cookie into a Bearer,
  // and if nginx pointed straight at FastAPI that translation would disappear and login and
  // every API call would break.
  //
  // nginx `client_max_body_size 11m`: the attachment upload cap (10MB,
  // backend/aipds/parsers/uploads.py) + multipart headroom. This conf is used only at boot, so
  // running instances are brought to the same value by infra/scripts/aipds-update — the two values
  // must match (test/user-data.assert.ts compares them).
  // ---------------------------------------------------------------------------
  return `#!/bin/bash
set -euxo pipefail
# 600: 이 로그는 시크릿 대입을 담을 수 있고, 644면 빌드 에이전트 계정이 읽는다.
touch /var/log/aipds-bootstrap.log
chmod 600 /var/log/aipds-bootstrap.log
exec > >(tee -a /var/log/aipds-bootstrap.log) 2>&1

# --- 패키지 (AL2023: awscli2는 기본 탑재). shadow-utils = useradd, git = 코드 배포. ---
dnf install -y python3.11 python3.11-devel gcc nodejs20 nodejs20-npm nginx tar unzip shadow-utils git

# --- 서비스 유저 (멱등: 재부트스트랩 시 이미 있을 수 있다) ---
id -u ${SVC} >/dev/null 2>&1 || useradd --system --create-home --shell /sbin/nologin ${SVC}

# --- 코드: 공개 리포 clone → 배포 브랜치 최신 커밋 (근거는 lib/deploy-source.ts) ---
# -f/safe.directory/분기는 cloud-init 재실행 때문이다. 빼면 부팅이 중단되고 증상은 502뿐.
# safe.directory가 둘인 이유: steering-files/는 서브모듈이라 **자기 git 디렉터리를
# 가진 별개의 리포**다. 재실행 때 트리는 이미 ${SVC} 소유이고 이 스크립트는 root로
# 도는데, git은 리포마다 소유권을 따로 본다 — 앱 트리만 등록하면 서브모듈 쪽이
# dubious ownership으로 막힌다.
git config --system --add safe.directory ${APP}
git config --system --add safe.directory ${APP}/steering-files
if [ -d ${APP}/.git ]; then
  git -C ${APP} fetch --prune origin
else
  rm -rf ${APP}
  git clone ${repoUrl} ${APP}
fi
git -C ${APP} checkout -f -B ${branch} origin/${branch}
# AI-PLC 룰셋은 상류 리포(aws-samples/sample-ai-plc)의 서브모듈로 들어 있고
# **clone은 서브모듈 내용을 가져오지 않는다** — 이 줄을 빼면 steering-files/가 빈
# 디렉터리로 남고, 부팅은 성공하며 증상은 첫 Discovery 턴에서 룰을 못 찾는 것뿐이다.
# --remote가 아니라 --init인 것도 의도다: 배포는 리포가 **고정한 커밋**을 쓴다.
# 상류 갱신을 받는 것은 개발자가 'git submodule update --remote' 후 커밋하는 일이고,
# 그래야 "지금 도는 룰이 어느 커밋인가"에 답할 수 있다.
git -C ${APP} submodule update --init --recursive
# 배포에 SHA가 없으므로 "무엇이 도는가"의 답이 이 한 줄에만 남는다.
git -C ${APP} --no-pager log -1 --format='booted commit: %H %s'
git -C ${APP} --no-pager submodule status --recursive
cd ${APP}

# --- 산출물·설정 디렉토리. 두 config dir은 반드시 서로 다른 경로다 ---
mkdir -p ${APP}/protos ${APP}/proto-config ${APP}/workspaces ${APP}/discovery-config

# --- 소유권: 이후 모든 것이 서비스 유저로 돈다 ---
chown -R ${SVC}:${SVC} ${APP}

# --- 백엔드: venv + 설치 (서비스 유저로 — venv 소유권이 어긋나지 않게) ---
runuser -u ${SVC} -- python3.11 -m venv ${APP}/backend/.venv
runuser -u ${SVC} -- ${APP}/backend/.venv/bin/pip install --upgrade pip
runuser -u ${SVC} -- ${APP}/backend/.venv/bin/pip install -e ${APP}/backend
# claude-agent-sdk는 하한만 걸려 있어(backend/pyproject.toml) 위 install이 캐시된
# 낡은 wheel로 만족할 수 있다 — 최신 엔진을 받으려면 -U로 따로 올린다.
runuser -u ${SVC} -- ${APP}/backend/.venv/bin/pip install -U claude-agent-sdk

# --- Cognito 클라이언트 시크릿 (부팅 조회 — CFN 템플릿에 평문으로 남기지 않는다) ---
# set +x 필수: xtrace는 대입 결과를 로그에 남기고, 그 로그는 빌드 에이전트 계정이 읽는다.
set +x
COGNITO_SECRET=$(aws cognito-idp describe-user-pool-client \\
  --user-pool-id ${userPoolId} --client-id ${userPoolClientId} \\
  --query 'UserPoolClient.ClientSecret' --output text --region ${region})
set -x

# --- 프론트: 빌드 (same-origin /api) ---
cd ${APP}/frontend
runuser -u ${SVC} -- env NEXT_PUBLIC_API_BASE_URL=/api HOME=${APP} npm ci
runuser -u ${SVC} -- env NEXT_PUBLIC_API_BASE_URL=/api HOME=${APP} npm run build

# --- 비밀 헤더 값 (부팅 조회). 위와 같은 이유로 xtrace를 끈다 ---
set +x
SECRET=$(aws secretsmanager get-secret-value --secret-id ${secretArn} --query SecretString --output text --region ${region})
set -x

# --- nginx: 헤더 검증 + 라우팅 ---
cat > /etc/nginx/conf.d/aipds.conf <<NGINX
server {
  listen 80 default_server;
  server_name _;
  client_max_body_size 11m;
  # JWT 쿠키 3개가 기본 버퍼(4k/8k)를 넘겨 400이 난다 — 응답 쪽 버퍼와 짝이다.
  large_client_header_buffers 4 32k;

  # /api/* 응답은 여기서 압축한다. Next의 /api 프록시는 fetch가 풀어 준 본문을
  # content-encoding 없이 다시 내보내고, CloudFront는 CACHING_DISABLED라 압축하지
  # 않는다 -- 그대로 두면 프로토타입 프리뷰의 JS·HTML·RSC가 3~10배로 불어나
  # 첫 진입 동안 링크 클릭이 그 다운로드 뒤에 줄을 선다.
  # proxied any: CloudFront가 Via를 붙이므로 기본값(off)이면 아무것도 압축하지 않는다.
  # text/event-stream은 일부러 목록에 없다 -- 압축하면 SSE 이벤트가 버퍼에 갇힌다.
  gzip on;
  gzip_proxied any;
  gzip_vary on;
  gzip_min_length 1024;
  gzip_types text/css text/plain text/javascript application/javascript application/json text/x-component image/svg+xml;

  # CloudFront 비밀 헤더 불일치(직접 스캔·타인 배포)는 무조건 차단.
  if (\\$http_x_origin_verify != "\${SECRET}") { return 403; }

  # /api/*도 여기로 온다. FastAPI를 직접 가리키면 쿠키→Bearer 번역이 사라져 다 깨진다.
  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host \\$host;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_buffering off;          # SSE 즉시 전달 (browser -> nginx -> Next -> FastAPI)
    # 세 값을 **함께** 올려야 한다. buffer_size만 키우면 nginx가 설정을 거부하고,
    # 안 키우면 로그인 콜백의 Set-Cookie 3개가 넘쳐 502다. 근거는 lib/user-data.ts 주석.
    proxy_buffer_size 32k;
    proxy_buffers 8 32k;
    proxy_busy_buffers_size 64k;
    proxy_read_timeout 3600s;
  }
}
NGINX
# AL2023 기본 server 블록 제거(default_server 충돌). 앵커가 주석 예시를 건드리지 않는다.
sed -i '/^    server {/,/^    }/d' /etc/nginx/nginx.conf

# --- systemd 유닛 ---
cat > /etc/systemd/system/aipds-backend.service <<UNIT
[Unit]
Description=AI-PDS backend (FastAPI/uvicorn)
After=network.target
[Service]
# non-root 필수 — Claude Code가 root에서 bypassPermissions를 거부한다(위 주석 참조).
User=${SVC}
Group=${SVC}
WorkingDirectory=${APP}/backend
Environment=HOME=${APP}
Environment=AWS_REGION=${region}
Environment=AWS_DEFAULT_REGION=${region}
Environment=AIPDS_S3_REGION=${region}
Environment=AIPDS_S3_BUCKET=${bucketName}
Environment=ANTHROPIC_MODEL=${model}
Environment=AIPDS_PROTO_ROOT=${APP}/protos
Environment=AIPDS_WORKSPACES_DIR=${APP}/workspaces
# 프로토타입 빌드: 동시 빌드 상한과 빌드 에이전트 전용 CLAUDE_CONFIG_DIR.
# 후자를 비우면 번들 Claude Code 바이너리가 서비스 유저의 ~/.claude를 읽는다 —
# 앱 트리 안에 두어 소유권·백업·정리를 APP 한 경로로 통일한다.
Environment=AIPDS_PROTO_MAX_CONCURRENT=10
Environment=AIPDS_PROTO_CONFIG_DIR=${APP}/proto-config
# proto-config와 반드시 다른 경로(공유하면 Discovery가 shadcn-design을 켠 채로 돈다).
Environment=AIPDS_DISCOVERY_CONFIG_DIR=${APP}/discovery-config
# 이 두 값이 비면 백엔드가 모든 요청을 통과시킨다 — 배포에서는 반드시 채워진다.
Environment=AIPDS_COGNITO_USER_POOL_ID=${userPoolId}
Environment=AIPDS_COGNITO_CLIENT_ID=${userPoolClientId}
Environment=AIPDS_COGNITO_REGION=${region}
# 빼면 Secure가 생략된다 — 증상이 없어 눈으로 안 잡히므로 assert가 존재를 단정한다.
Environment=AIPDS_COOKIE_SECURE=true
# 이 둘은 **함께** 켠다(cli_settings.py). 윈도우만 올리면 컴팩션 전에 한도에 부딪힌다.
Environment=AIPDS_AUTO_COMPACT_WINDOW=750000
Environment=AIPDS_LONG_CONTEXT=true
ExecStart=${APP}/backend/.venv/bin/uvicorn aipds.app:app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/aipds-frontend.service <<UNIT
[Unit]
Description=AI-PDS frontend (Next.js)
After=network.target
[Service]
User=${SVC}
Group=${SVC}
WorkingDirectory=${APP}/frontend
Environment=NODE_ENV=production
Environment=HOME=${APP}
# NEXT_PUBLIC_ 접두어 금지 — 붙이면 시크릿이 클라이언트 번들로 나간다.
Environment=COGNITO_HOSTED_UI_DOMAIN=${hostedUiDomain}
Environment=COGNITO_CLIENT_ID=${userPoolClientId}
Environment=COGNITO_CLIENT_SECRET=\${COGNITO_SECRET}
Environment=APP_BASE_URL=${appUrl}
ExecStart=/usr/bin/npx next start -H 127.0.0.1 -p 3000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

# 코드 갱신 경로. 스크립트 본문과 그 이유는 infra/scripts/aipds-update에 있다.
cat > /etc/aipds-deploy.env <<ENV
APP=${APP}
SVC=${SVC}
BRANCH=${branch}
ENV
install -m 755 ${APP}/infra/scripts/aipds-update /usr/local/bin/aipds-update

# 샌드박스: 설치 + SSM에 값이 있으면 래퍼·IMDS 차단·프리뷰까지. 서비스 기동 전이어야 한다(scripts/aipds-harden boot).
AWS_REGION=${region} ${APP}/infra/scripts/aipds-harden boot

systemctl daemon-reload
systemctl enable --now nginx aipds-backend aipds-frontend
`;
}
