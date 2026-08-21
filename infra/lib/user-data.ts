export interface UserDataOptions {
  region: string;
  bucketName: string;
  model: string;
  secretArn: string;
  /** 공개 리포의 HTTPS clone URL. */
  repoUrl: string;
  /**
   * 배포 대상 브랜치. 부팅 때 이 브랜치의 **원격 최신 커밋**으로 맞춘다.
   *
   * 이 값이 커밋 SHA가 아니라 브랜치라는 것의 결과: user-data가 코드 변경과
   * 무관하게 동일하므로 `cdk deploy`는 인스턴스를 교체하지 않는다. 코드 갱신은
   * 아래에서 설치하는 `aipds-update`가 한다. 근거는 lib/deploy-source.ts.
   */
  branch: string;
  // 인증. 이 값들이 비면 백엔드가 인증 바이패스로 돌아 배포가 무인증으로
  // 공개된다 — 호스팅 스택이 항상 채운다.
  userPoolId: string;
  userPoolClientId: string;
  hostedUiDomain: string;
  appUrl: string;
}

// EC2 부트스트랩 스크립트. 순수 문자열 생성(부수효과 없음) — 단위 테스트 가능.
// 부팅 시: 패키지 설치 → 서비스 유저 생성 → 리포 clone → 백엔드 venv/설치 →
// 프론트 빌드 → 시크릿 조회 → nginx conf → systemd 기동. 헤더 불일치는 nginx가 403.
export function renderUserData(opts: UserDataOptions): string {
  const { region, bucketName, model, secretArn, repoUrl, branch,
          userPoolId, userPoolClientId, hostedUiDomain, appUrl } = opts;
  const APP = '/opt/aipds';
  // 서비스 전용 non-root 유저. 편의가 아니라 필수다: 프로토타입 빌드 에이전트가
  // 띄우는 Claude Code 바이너리는 euid==0에서 bypassPermissions를 거부한다
  // (6d21e1f에서 실측 — `--version`은 root에서도 성공해 이 실패를 가리므로,
  // 부팅은 정상으로 보이고 첫 빌드 턴에서야 502로 드러난다). MicroVM 이미지가
  // non-root 'harness' 유저를 쓴 이유가 이것이고, 빌드가 백엔드 프로세스로
  // 흡수된 지금은 백엔드 자체가 non-root여야 한다.
  const SVC = 'aipds';
  // ---------------------------------------------------------------------------
  // 아래 템플릿 문자열의 주석은 **EC2 user-data 16KB 한계를 쓴다.** 한 번 넘겨서
  // 배포가 InvalidRequest로 실패한 적이 있다(18,158바이트, 그중 주석 12KB·한글이
  // 3바이트/자). 그래서 "왜"는 여기(TS 주석 = 바이트 0)에 적고, 템플릿 안에는
  // 인스턴스에서 그 스크립트를 읽는 사람이 **깨뜨리지 않기 위해** 알아야 하는
  // 한 줄만 남긴다. 긴 절차 하나(aipds-update)는 아예 리포 파일로 나가 있다
  // (infra/scripts/aipds-update — 그 파일의 주석은 예산과 무관하다).
  //
  // 부트스트랩 로그를 600으로 잠그는 이유: 644면 서비스 유저가 읽을 수 있고, 그
  // 계정은 프로토타입 빌드 에이전트를 bypassPermissions로 돌리는 계정이다. 아래
  // 두 곳의 `set +x`로 시크릿 대입을 가리지만, 그것만 믿지 않고 파일 자체를 잠근다.
  //
  // S3 에셋 zip 대신 git clone인 이유, 커밋 SHA 대신 브랜치인 이유: lib/deploy-source.ts.
  // 요지는 (1) 에셋은 gitignore된 파일까지 실어 보정 목록이 필요했고 배포되는 것이
  // 커밋이 아니라 워킹 트리였다, (2) 배포자가 "이 커밋을 푸시했는가"를 신경 쓰지
  // 않아도 되게 한다. 대가는 cdk deploy가 코드를 갱신하지 못하는 것이고, 그 자리를
  // aipds-update가 메운다.
  //
  // `checkout -f -B`가 첫 clone과 cloud-init 재실행에서 같은 결과를 내야 한다. -f가
  // 필요한 쪽은 재실행이다 — 수정된 tracked 파일이 하나라도 있으면 -f 없는 checkout은
  // 거부되고, set -e 아래에서 그 거부는 부팅 중단(증상은 502)이다. untracked
  // (protos/·workspaces/·세션 상태)는 -f로도 지워지지 않는다.
  //
  // `safe.directory`를 미리 넣는 이유: 아래에서 트리를 SVC 소유로 넘기므로 재실행 때
  // root로 도는 git이 "dubious ownership"으로 거부되고, 그것도 부팅 중단이다.
  //
  // 두 CLAUDE_CONFIG_DIR(proto-config·discovery-config)을 앱 트리 안에 두고 **서로 다른
  // 경로**로 두는 이유: 앱 소유 데이터를 한 경로로 모으고, 공유하면 skills="all" 때문에
  // Discovery가 프로토타입 빌드용 shadcn-design 스킬을 켠 채로 돈다.
  //
  // ⚠️ 이 파일은 TS 템플릿 리터럴이다 — **주석에도 백틱을 쓰지 말 것.** 백틱 하나가
  // 리터럴을 닫아 user-data 전체가 파싱 에러가 된다(템플릿 안 주석에도 해당).
  //
  // AIPDS_COOKIE_SECURE: 프로토타입 접근 쿠키의 Secure 스위치
  // (routes/proto_public.py의 _cookie_secure). 빼면 기본값(꺼짐)으로 Secure가
  // 생략되고, CloudFront 때문에 실동작은 정상으로 보이지만 쿠키는 평문 HTTP로도
  // 전송될 수 있는 상태로 남는다 — 화면 증상이 없어 눈으로 안 잡히므로
  // user-data.assert.ts가 이 줄의 존재를 단정한다(한 번 빠뜨린 적이 있다).
  // 로컬 개발에서는 켜지 않는다(localhost에서 브라우저가 Secure 쿠키를 저장하지 않아
  // 프리뷰가 열리지 않는다).
  //
  // AIPDS_AUTO_COMPACT_WINDOW / AIPDS_LONG_CONTEXT: 둘은 함께 켠다
  // (cli_settings.py). 윈도우만 올리고 1M을 켜지 않으면 컴팩션 전에 모델 컨텍스트
  // 한도에 부딪힌다. 왜 켜는가: 실측(2026-08-13)에서 빌드 세션이 264,040 → 53,375
  // 토큰으로 요약됐고, 요약 뒤 후반 스테이지 문서는 근거가 아니라 요약에서 나와
  // 뒤로 갈수록 얇아진다. 한국어는 같은 대화에 토큰을 1.66배 써서 그 지점에 40%
  // 일찍 도달한다. 750000은 발동 지점의 약 4배이면서 1M 아래 마진을 남기는 값이다.
  // 대가는 턴당 비용(컴팩션이 늦으면 전체 이력이 매 턴 재전송된다 — 캐시 리드는
  // 0.1배지만 0이 아니다) — 워크숍 비용이 문제면 먼저 이 값을 내린다. Bedrock에서
  // 1M 접미사가 필요한 이유는 cli_settings.cli_model_id의 docstring에 있다.
  //
  // 프론트 유닛: HOME이 쓰기 가능해야 npx가 캐시를 쓴다(root 홈은 못 쓴다).
  // COGNITO_* 에 NEXT_PUBLIC_ 접두어를 붙이면 안 된다 — 클라이언트 번들에
  // 인라인되어 시크릿이 브라우저로 나간다. CLIENT_SECRET을 인용 없이 확장하는 것은
  // 안전하다(Cognito 시크릿은 [A-Za-z0-9_+]{24,64}로 셸 메타문자가 없다).
  //
  // nginx: 로그인 후 요청이 access/id/refresh JWT 쿠키 세 개를 싣는다 — 기본 버퍼
  // (4k/8k)로는 Cookie 헤더가 넘쳐 400이 나므로 요청·응답 양쪽 버퍼를 올린다.
  // `/api/` 전용 location을 두지 않는 것도 의도다: /api/*는 Next의
  // app/api/[...path]/route.ts가 서버사이드에서 FastAPI로 넘기며 쿠키를 Bearer로
  // 번역하는 지점이고, nginx가 FastAPI를 직접 가리키면 그 번역이 사라져 로그인과
  // 모든 API 호출이 깨진다.
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
  client_max_body_size 6m;
  # JWT 쿠키 3개가 기본 버퍼(4k/8k)를 넘겨 400이 난다 — 응답 쪽 버퍼와 짝이다.
  large_client_header_buffers 4 32k;

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

systemctl daemon-reload
systemctl enable --now nginx aipds-backend aipds-frontend
`;
}
