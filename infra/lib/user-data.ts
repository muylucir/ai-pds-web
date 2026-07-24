export interface UserDataOptions {
  region: string;
  bucketName: string;
  model: string;
  secretArn: string;
  assetS3Uri: string;
}

// EC2 부트스트랩 스크립트. 순수 문자열 생성(부수효과 없음) — 단위 테스트 가능.
// 부팅 시: 패키지 설치 → 에셋 전개 → 백엔드 venv/설치 → 프론트 빌드 →
// 시크릿 조회 → nginx conf → systemd 기동. 헤더 불일치는 nginx가 403.
export function renderUserData(opts: UserDataOptions): string {
  const { region, bucketName, model, secretArn, assetS3Uri } = opts;
  const APP = '/opt/pathfinder';
  return `#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/pathfinder-bootstrap.log) 2>&1

# --- 패키지 (AL2023: awscli2는 기본 탑재) ---
dnf install -y python3.11 python3.11-devel gcc nodejs20 nodejs20-npm nginx tar unzip

# --- 에셋 전개 ---
mkdir -p ${APP}
cd ${APP}
aws s3 cp ${assetS3Uri} /tmp/app.zip --region ${region}
unzip -o /tmp/app.zip -d ${APP}
rm -f /tmp/app.zip

# --- 백엔드: venv + 설치 ---
cd ${APP}/backend
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# --- 프론트: 빌드 (same-origin /api) ---
cd ${APP}/frontend
export NEXT_PUBLIC_API_BASE_URL=/api
npm ci
npm run build

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
WorkingDirectory=${APP}/backend
Environment=AWS_REGION=${region}
Environment=AWS_DEFAULT_REGION=${region}
Environment=PATHFINDER_S3_REGION=${region}
Environment=PATHFINDER_S3_BUCKET=${bucketName}
Environment=ANTHROPIC_MODEL=${model}
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
WorkingDirectory=${APP}/frontend
Environment=NODE_ENV=production
ExecStart=/usr/bin/npx next start -H 127.0.0.1 -p 3000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now nginx pathfinder-backend pathfinder-frontend
`;
}
