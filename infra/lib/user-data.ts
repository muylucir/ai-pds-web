export interface UserDataOptions {
  region: string;
  bucketName: string;
  model: string;
  secretArn: string;
  /** 공개 리포의 HTTPS clone URL. */
  repoUrl: string;
  /**
   * 배포할 커밋. **브랜치 이름을 넣으면 안 된다** — 값이 그대로 이 스크립트에
   * 들어가므로, 바뀌지 않는 값은 user-data를 바꾸지 않고 따라서 CloudFormation이
   * 인스턴스를 교체하지 않는다(UserData는 replacement 속성이다). 그러면 배포가
   * 코드 갱신 수단이 아니게 된다. 근거와 결정 규칙은 lib/deploy-source.ts.
   */
  ref: string;
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
  const { region, bucketName, model, secretArn, repoUrl, ref,
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
# 644(디폴트)면 pathfinder 서비스 유저(=bypassPermissions로 도는 프로토타입
# 빌드 에이전트가 쓰는 그 계정)가 이 로그를 읽을 수 있다 — set +x로 감싼
# 시크릿 대입도 못 미더워 파일 자체를 root 전용으로 잠근다.
touch /var/log/pathfinder-bootstrap.log
chmod 600 /var/log/pathfinder-bootstrap.log
exec > >(tee -a /var/log/pathfinder-bootstrap.log) 2>&1

# --- 패키지 (AL2023: awscli2는 기본 탑재). shadow-utils = useradd, git = 코드 배포. ---
dnf install -y python3.11 python3.11-devel gcc nodejs20 nodejs20-npm nginx tar unzip shadow-utils git

# --- 서비스 유저 (멱등: 재부트스트랩 시 이미 있을 수 있다) ---
id -u ${SVC} >/dev/null 2>&1 || useradd --system --create-home --shell /sbin/nologin ${SVC}

# --- 코드: 공개 리포를 clone 하고 배포 대상 커밋에 고정 ---
# S3 에셋 zip을 쓰지 않는 이유는 lib/deploy-source.ts에 있다(요지: 에셋은
# gitignore된 파일까지 실어 보정 목록이 필요했고, 배포되는 것이 커밋이 아니라
# 워킹 트리였다). clone은 tracked 파일만 가져오므로 그 두 문제가 함께 사라진다.
#
# --detach로 커밋에 직접 붙는 이유: 브랜치를 만들면 나중에 SSM으로 들어가
# 'git pull'을 했을 때 그 브랜치가 배포 커밋보다 앞서 가고, 그러면 인스턴스가
# 무엇을 돌리는지가 다시 흐려진다. detached HEAD는 "이 커밋을 돌린다"를 그대로
# 드러낸다(핫픽스는 의도적으로 'git checkout main && git pull'을 해야 한다).
#
# 멱등: 같은 인스턴스에서 다시 돌 수 있다(cloud-init 재실행). .git이 있으면
# fetch만 하고, 없으면 새로 clone한다 — 중간에 끊긴 clone이 남긴 부분 트리는
# .git이 없으므로 이 분기에서 정리된다. 첫 부팅에는 ${APP}이 비어 있으므로
# 여기서 지워지는 사용자 데이터는 없다(protos/는 아래에서 만들어진다).
#
# safe.directory를 미리 넣는다: 아래에서 트리를 ${SVC} 소유로 넘기므로, 재실행
# 때 root로 도는 이 git 명령이 "detected dubious ownership"으로 거부된다.
# set -e 아래에서 그 거부는 부팅 중단이고, 증상은 502뿐이다.
git config --system --add safe.directory ${APP}
if [ -d ${APP}/.git ]; then
  git -C ${APP} fetch --prune origin
else
  rm -rf ${APP}
  git clone ${repoUrl} ${APP}
fi
git -C ${APP} checkout --detach ${ref}
cd ${APP}

# --- 빌드 산출물·설정 디렉토리를 앱 트리 안에 만든다 ---
# CLAUDE_CONFIG_DIR을 유저 홈이 아니라 APP 트리에 두는 이유: 앱 소유 데이터를
# 한 경로 아래로 모아 백업·정리·권한을 한 번에 다루고, 서비스 유저의 홈
# 디렉토리 위치 변경에 의존하지 않게 한다.
mkdir -p ${APP}/protos ${APP}/proto-config ${APP}/workspaces
# Discovery 에이전트 전용 CLAUDE_CONFIG_DIR — proto-config와 같은 이유로 앱
# 트리 안에 두고, 반드시 다른 경로다(공유하면 skills="all" 때문에 Discovery가
# 프로토타입 빌드용 shadcn-design 스킬을 켠 채로 돈다).
mkdir -p ${APP}/discovery-config

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
# set +x로 감싸는 이유: -x(xtrace)는 명령과 그 결과인 대입문을 둘 다 그대로
# /var/log/pathfinder-bootstrap.log(644)에 남긴다 — 그 로그는 프로토타입 빌드
# 에이전트를 bypassPermissions로 돌리는 같은 서비스 유저가 읽을 수 있으므로,
# 켜둔 채로는 시크릿이 모델이 생성한 코드에 노출된다.
set +x
COGNITO_SECRET=$(aws cognito-idp describe-user-pool-client \\
  --user-pool-id ${userPoolId} --client-id ${userPoolClientId} \\
  --query 'UserPoolClient.ClientSecret' --output text --region ${region})
set -x

# --- 프론트: 빌드 (same-origin /api) ---
cd ${APP}/frontend
runuser -u ${SVC} -- env NEXT_PUBLIC_API_BASE_URL=/api HOME=${APP} npm ci
runuser -u ${SVC} -- env NEXT_PUBLIC_API_BASE_URL=/api HOME=${APP} npm run build

# --- 비밀 헤더 값 (부팅 시 조회, 하드코딩 안 함) ---
# 위 COGNITO_SECRET과 같은 이유로 xtrace를 끈다 — 이 값도 부트스트랩 로그에
# 그대로 남으면 같은 서비스 유저(=빌드 에이전트)가 읽을 수 있다.
set +x
SECRET=$(aws secretsmanager get-secret-value --secret-id ${secretArn} --query SecretString --output text --region ${region})
set -x

# --- nginx: 헤더 검증 + 라우팅 ---
cat > /etc/nginx/conf.d/pathfinder.conf <<NGINX
server {
  listen 80 default_server;
  server_name _;
  client_max_body_size 6m;
  # 로그인 후 모든 요청이 access/id/refresh JWT 쿠키 세 개를 실어 보낸다.
  # 기본값(4k/8k)으로는 그 Cookie 헤더가 넘쳐 400(Request Header Or Cookie Too
  # Large)이 난다 — 응답 쪽 proxy_buffer_size와 짝이다.
  large_client_header_buffers 4 32k;

  # CloudFront가 붙인 비밀 헤더 불일치(직접 스캔·타인 배포)는 무조건 차단.
  if (\\$http_x_origin_verify != "\${SECRET}") { return 403; }

  # /api/*도 여기로 온다 — Next의 app/api/[...path]/route.ts가 same-origin
  # 프록시로서 서버사이드에서 FastAPI(:8000)로 넘긴다(쿠키를 Bearer로 번역하는
  # 지점이기도 하다). FastAPI를 nginx가 직접 가리키면 그 번역이 일어나지
  # 않아 로그인·모든 API 호출이 깨진다 — /api/ 전용 location을 두지 않는다.
  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host \\$host;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_buffering off;          # SSE 즉시 전달 (browser -> nginx -> Next -> FastAPI)
    # 응답 헤더 버퍼. proxy_buffering이 꺼져 있어도 nginx는 응답 **헤더**를 이
    # 버퍼 하나에 담는다. 기본값(페이지 크기, 보통 4k)으로는 로그인 콜백이
    # 내보내는 access/id/refresh JWT 세 개의 Set-Cookie가 넘쳐
    # "upstream sent too big header"로 502가 난다(실측: 로그인 시 502).
    #
    # ⚠️ proxy_buffer_size만 키우면 nginx가 설정을 거부한다(실측):
    #   "proxy_busy_buffers_size must be less than the size of all
    #    proxy_buffers minus one buffer"
    # busy_buffers 기본값이 buffer_size의 2배로 따라 올라가면서 기본
    # proxy_buffers(8x4k=32k)와의 제약을 깨기 때문이다. 세 값을 함께 올려
    # 제약(busy < buffers 총합 - 1개)을 만족시킨다: 64k < 8*32k - 32k.
    proxy_buffer_size 32k;
    proxy_buffers 8 32k;
    proxy_busy_buffers_size 64k;
    proxy_read_timeout 3600s;
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
Environment=PATHFINDER_PROTO_MAX_CONCURRENT=10
Environment=PATHFINDER_PROTO_CONFIG_DIR=${APP}/proto-config
# Discovery 드라이버(기본 claude, PATHFINDER_DISCOVERY_DRIVER로 strands 되돌림
# 가능) 전용 CLAUDE_CONFIG_DIR. proto-config와 반드시 다른 경로 — 공유하면
# Discovery가 프로토타입 빌드용 shadcn-design 스킬을 켠 채로 돈다.
Environment=PATHFINDER_DISCOVERY_CONFIG_DIR=${APP}/discovery-config
# 인증: 이 두 값이 비면 백엔드가 모든 요청을 통과시킨다(로컬 개발용 바이패스).
# 배포에서는 반드시 채워져야 한다.
Environment=PATHFINDER_COGNITO_USER_POOL_ID=${userPoolId}
Environment=PATHFINDER_COGNITO_CLIENT_ID=${userPoolClientId}
Environment=PATHFINDER_COGNITO_REGION=${region}
# 프로토타입 접근 쿠키에 Secure를 붙이는 스위치(routes/proto_public.py의
# _cookie_secure). CloudFront가 HTTPS를 강제하므로 배포에서는 항상 켠다.
#
# 이 줄이 없으면 백엔드는 기본값(꺼짐)으로 Secure를 **생략**한다 — CloudFront
# 때문에 실동작은 문제없어 보이지만, 쿠키 자체는 평문 HTTP로도 전송될 수 있는
# 상태로 남는다. 화면상 증상이 없어 눈으로는 잡히지 않으므로 user-data.assert.ts가
# 이 줄의 존재를 단정한다(실제로 한 번 빠뜨렸다).
#
# 로컬 개발에서는 설정하지 않는다: http://localhost에서 브라우저가 Secure 쿠키를
# 저장하지 않아 프리뷰가 열리지 않는다.
Environment=PATHFINDER_COOKIE_SECURE=true
# 컨텍스트 설정 두 개(backend/pathfinder/cli_settings.py). 둘은 **함께** 켠다:
# 윈도우만 올리고 1M을 켜지 않으면 컴팩션 전에 모델의 컨텍스트 한도에 부딪힌다.
#
# 왜 켜는가: 실측(2026-08-13)에서 빌드 세션이 컨텍스트 264,040 → 53,375 토큰으로
# 요약됐다. 요약 뒤에 쓰이는 후반 스테이지 문서는 근거가 아니라 요약에서 나오므로
# 뒤로 갈수록 얇아지고, 한국어는 같은 대화에 토큰을 1.66배 써서 그 지점에 40%
# 일찍 도달한다. 750000은 지금 발동 지점의 약 4배이면서 1M 상한 아래 마진을
# 남기는 값이다.
#
# 대가는 턴당 비용이다 — 컴팩션이 늦어지면 전체 이력이 매 턴 재전송된다(캐시
# 리드는 0.1배지만 0이 아니다). 워크숍 비용이 문제가 되면 먼저 이 값을 내린다.
#
# 1M 접미사가 Bedrock에서 필요한 이유는 cli_settings.cli_model_id의 docstring에
# 있다(Opus는 native_1m_3p가 없어 베타를 켜야 한다). 로컬 개발에서는 둘 다
# 설정하지 않는다 — 기본값이 꺼짐이고, 그 상태가 종전 동작이다.
#
# (이 파일은 TS 템플릿 리터럴이다 — 주석에도 백틱을 쓰지 말 것. 백틱 하나가
# 리터럴을 닫아 user-data 전체가 파싱 에러가 된다.)
Environment=PATHFINDER_AUTO_COMPACT_WINDOW=750000
Environment=PATHFINDER_LONG_CONTEXT=true
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
# 인용 없이 그대로 확장한다 — Cognito 클라이언트 시크릿은 [A-Za-z0-9_+]{24,64}
# 알파벳만 쓰므로(공백·따옴표·$ 등 셸 메타문자 없음) 이 heredoc 확장이 안전하다.
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
