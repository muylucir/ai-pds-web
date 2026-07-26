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

// EC2 부트스트랩 스크립트. 순수 문자열 생성(부수효과 없음) — 단위 테스트 가능.
// 부팅 시: 패키지 설치 → 서비스 유저 생성 → 에셋 전개 → 백엔드 venv/설치 →
// 프론트 빌드 → 시크릿 조회 → nginx conf → systemd 기동. 헤더 불일치는 nginx가 403.
export function renderUserData(opts: UserDataOptions): string {
  const { region, bucketName, model, secretArn, assetS3Uri,
          userPoolId, userPoolClientId, hostedUiDomain, appUrl } = opts;
  const APP = '/opt/pathfinder';
  // 서비스 전용 non-root 유저. 편의가 아니라 필수다: 프로토타입 빌드 에이전트가
  // 띄우는 Claude Code 바이너리는 euid==0에서 bypassPermissions를 거부한다
  // (6d21e1f에서 실측 — `--version`은 root에서도 성공해 이 실패를 가리므로,
  // 부팅은 정상으로 보이고 첫 빌드 턴에서야 502로 드러난다). MicroVM 이미지가
  // non-root 'harness' 유저를 쓴 이유가 이것이고, 빌드가 백엔드 프로세스로
  // 흡수된 지금은 백엔드 자체가 non-root여야 한다.
  const SVC = 'pathfinder';
  return `#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/pathfinder-bootstrap.log) 2>&1

# --- 패키지 (AL2023: awscli2는 기본 탑재). shadow-utils = useradd. ---
dnf install -y python3.11 python3.11-devel gcc nodejs20 nodejs20-npm nginx tar unzip shadow-utils

# --- 서비스 유저 (멱등: 재부트스트랩 시 이미 있을 수 있다) ---
id -u ${SVC} >/dev/null 2>&1 || useradd --system --create-home --shell /sbin/nologin ${SVC}

# --- 에셋 전개 ---
mkdir -p ${APP}
cd ${APP}
aws s3 cp ${assetS3Uri} /tmp/app.zip --region ${region}
unzip -o /tmp/app.zip -d ${APP}
rm -f /tmp/app.zip

# --- 빌드 산출물·설정 디렉토리를 앱 트리 안에 만든다 ---
# CLAUDE_CONFIG_DIR을 유저 홈이 아니라 APP 트리에 두는 이유: 앱 소유 데이터를
# 한 경로 아래로 모아 백업·정리·권한을 한 번에 다루고, 서비스 유저의 홈
# 디렉토리 위치 변경에 의존하지 않게 한다.
mkdir -p ${APP}/protos ${APP}/proto-config ${APP}/workspaces

# --- 소유권: 에셋 전개는 root가 했으므로 서비스 유저에게 넘긴다 ---
# 백엔드는 venv 실행, 프론트는 .next 읽기, 빌드 에이전트는 protos/ 쓰기가 필요하다.
chown -R ${SVC}:${SVC} ${APP}

# --- 백엔드: venv + 설치 (서비스 유저로 — venv 소유권이 어긋나지 않게) ---
runuser -u ${SVC} -- python3.11 -m venv ${APP}/backend/.venv
runuser -u ${SVC} -- ${APP}/backend/.venv/bin/pip install --upgrade pip
runuser -u ${SVC} -- ${APP}/backend/.venv/bin/pip install -e ${APP}/backend

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

# --- 비밀 헤더 값 (부팅 시 조회, 하드코딩 안 함) ---
SECRET=$(aws secretsmanager get-secret-value --secret-id ${secretArn} --query SecretString --output text --region ${region})

# --- nginx: 헤더 검증 + 라우팅 ---
cat > /etc/nginx/conf.d/pathfinder.conf <<NGINX
server {
  listen 80 default_server;
  server_name _;
  client_max_body_size 6m;

  # CloudFront가 붙인 비밀 헤더 불일치(직접 스캔·타인 배포)는 무조건 차단.
  if (\\$http_x_origin_verify != "\${SECRET}") { return 403; }

  location /api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host \\$host;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_buffering off;          # SSE 즉시 전달
    proxy_read_timeout 3600s;
  }
  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host \\$host;
    proxy_set_header X-Forwarded-Proto https;
  }
}
NGINX
# AL2023 기본 conf의 default_server와 충돌 방지 — 기본 server 블록 제거.
# 앵커(^    server {)로 실제(들여쓰기된, 주석 아닌) 블록만 매칭 — 주석 처리된
# TLS 예시 블록(#    server {)은 매칭하지 않으므로 파일 끝까지 삭제되는 사고 없음.
sed -i '/^    server {/,/^    }/d' /etc/nginx/nginx.conf

# --- systemd 유닛 ---
cat > /etc/systemd/system/pathfinder-backend.service <<UNIT
[Unit]
Description=Pathfinder backend (FastAPI/uvicorn)
After=network.target
[Service]
# non-root 필수 — Claude Code가 root에서 bypassPermissions를 거부한다(위 주석 참조).
User=${SVC}
Group=${SVC}
WorkingDirectory=${APP}/backend
Environment=HOME=${APP}
Environment=AWS_REGION=${region}
Environment=AWS_DEFAULT_REGION=${region}
Environment=PATHFINDER_S3_REGION=${region}
Environment=PATHFINDER_S3_BUCKET=${bucketName}
Environment=ANTHROPIC_MODEL=${model}
Environment=PATHFINDER_PROTO_ROOT=${APP}/protos
Environment=PATHFINDER_WORKSPACES_DIR=${APP}/workspaces
# 프로토타입 빌드: 동시 빌드 상한과 빌드 에이전트 전용 CLAUDE_CONFIG_DIR.
# 후자를 비우면 번들 Claude Code 바이너리가 서비스 유저의 ~/.claude를 읽는다 —
# 앱 트리 안에 두어 소유권·백업·정리를 APP 한 경로로 통일한다.
Environment=PATHFINDER_PROTO_MAX_CONCURRENT=2
Environment=PATHFINDER_PROTO_CONFIG_DIR=${APP}/proto-config
# 인증: 이 두 값이 비면 백엔드가 모든 요청을 통과시킨다(로컬 개발용 바이패스).
# 배포에서는 반드시 채워져야 한다.
Environment=PATHFINDER_COGNITO_USER_POOL_ID=${userPoolId}
Environment=PATHFINDER_COGNITO_CLIENT_ID=${userPoolClientId}
Environment=PATHFINDER_COGNITO_REGION=${region}
ExecStart=${APP}/backend/.venv/bin/uvicorn pathfinder.app:app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/pathfinder-frontend.service <<UNIT
[Unit]
Description=Pathfinder frontend (Next.js)
After=network.target
[Service]
User=${SVC}
Group=${SVC}
WorkingDirectory=${APP}/frontend
Environment=NODE_ENV=production
# npx가 캐시를 쓰려면 쓰기 가능한 HOME이 필요하다(root 홈은 이제 못 쓴다).
Environment=HOME=${APP}
# Hosted UI 왕복과 토큰 교환. NEXT_PUBLIC_ 접두어를 붙이면 안 된다 —
# 클라이언트 번들에 인라인되어 시크릿이 브라우저로 나간다.
Environment=COGNITO_HOSTED_UI_DOMAIN=${hostedUiDomain}
Environment=COGNITO_CLIENT_ID=${userPoolClientId}
Environment=COGNITO_CLIENT_SECRET=\${COGNITO_SECRET}
Environment=APP_BASE_URL=${appUrl}
ExecStart=/usr/bin/npx next start -H 127.0.0.1 -p 3000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now nginx pathfinder-backend pathfinder-frontend
`;
}
