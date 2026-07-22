# EC2 + CloudFront 호스팅 설계 (CDK)

날짜: 2026-07-22
상태: 설계 확정 (사용자 승인)

## 1. 목적

지금까지 로컬(dev 프록시)로만 돌던 Pathfinder(프론트 Next.js :3000 + 백엔드
FastAPI :8000)를 AWS에 호스팅한다. 요구사항:

- **CDK로 전부 관리** — 인스턴스, 네트워크, CloudFront까지 코드로.
- **EC2 접근은 CloudFront를 통해서만** — SG 인바운드를 CloudFront
  origin-facing **관리형 프리픽스 리스트**로 제한하고, 배포 리전에 맞는
  프리픽스 리스트 ID를 **자동으로** 조회한다(리전마다 ID가 다름).
- **커스텀 헤더 인증** — 프리픽스 리스트만으로는 "다른 사람의 CloudFront
  배포"도 통과하므로, CloudFront가 붙이는 비밀 헤더를 오리진에서 검증해
  우리 배포만 통과시킨다.

## 2. 아키텍처 개요

```
Browser ──HTTPS──▶ CloudFront (기본 도메인 dxxxx.cloudfront.net)
                      │  + X-Origin-Verify: <secret>   (오리진 커스텀 헤더)
                      ▼  HTTP :80  (SG: CF origin-facing 프리픽스 리스트만 허용)
                   EC2 (AL2023, t4g.medium, EIP)
                      └─ nginx :80 ── 헤더 불일치 → 403
                           ├─ /api/*  → 127.0.0.1:8000  (FastAPI, /api 프리픽스 제거)
                           └─ /*      → 127.0.0.1:3000  (next start)
```

- 뷰어↔CloudFront는 HTTPS(기본 인증서), CloudFront↔EC2는 HTTP-only.
  보안 경계는 SG 프리픽스 리스트(네트워크) + 비밀 헤더(애플리케이션) 이중.
- 앱 프로세스는 둘 다 루프백에만 바인드 — 외부에서 직접 도달 불가.
- 프론트는 `NEXT_PUBLIC_API_BASE_URL=/api`로 빌드 → 브라우저는 same-origin
  `/api/*`를 호출하고 nginx가 백엔드로 라우팅. 기존 Next 라우트 핸들러
  프록시(`app/api/[...path]/route.ts`)는 nginx가 먼저 가로채므로 프로덕션에서
  사용되지 않는다(코드 수정 없음). CORS도 same-origin이라 불필요.

## 3. 스택 구성

새 파일 `infra/lib/pathfinder-hosting-stack.ts`의 **PathfinderHostingStack**.
기존 `PathfinderDrillStack`(버킷+롤)은 그대로 두고, `bin/app.ts`에서 드릴
스택의 버킷을 prop으로 넘긴다. 두 스택 모두 동일한 리전 파라미터화
(`CDK_DEPLOY_REGION` > `CDK_DEFAULT_REGION` > 서울)를 따른다.

### 3.1 VPC

퍼블릭 서브넷만 있는 신규 VPC(NAT 없음, 비용 0). maxAzs 2.

### 3.2 보안 그룹 + 프리픽스 리스트 (자동 리전 대응)

```ts
const cfOriginFacing = ec2.PrefixList.fromLookup(this, 'CfOriginFacing', {
  prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
});
sg.addIngressRule(ec2.Peer.prefixList(cfOriginFacing.prefixListId), ec2.Port.tcp(80));
```

- `fromLookup`은 **배포 리전의** 관리형 프리픽스 리스트 ID를 컨텍스트
  프로바이더로 조회 — 리전을 바꿔도 코드 수정 없이 맞는 ID가 들어간다.
  조회 결과는 `cdk.context.json`에 캐시되므로 커밋한다.
- 그 외 인바운드 없음(SSH 포트 22 미개방 — 접속은 SSM Session Manager).
- 이그레스는 기본 전체 허용(패키지 설치·Bedrock·S3 호출용).

### 3.3 비밀 헤더 (Secrets Manager)

- `secretsmanager.Secret`으로 랜덤 시크릿 생성 (구두점 제외 32자).
- CloudFront 오리진 커스텀 헤더 `X-Origin-Verify`에 시크릿 값을 주입
  (CFN dynamic reference — 템플릿에는 참조만 남고 값은 배포 시 해석).
- EC2 user-data가 부팅 시 `aws secretsmanager get-secret-value`로 같은
  값을 받아 nginx 설정에 삽입. 인스턴스 롤에 해당 시크릿 읽기 권한 부여.
- 시크릿 로테이션은 범위 외(수동: 시크릿 값 갱신 → 인스턴스 리부트/재배포).

### 3.4 EC2 인스턴스

- **AL2023, arm64(Graviton), t4g.medium** (4GB — `next build`에 필요).
- 퍼블릭 서브넷 + **EIP** (`CfnEIP` + `CfnEIPAssociation`).
- 인스턴스 롤: 드릴 스택 BackendRole과 동일한 정책(Bedrock invoke +
  아티팩트 버킷 `projects/*`/`sessions/*` R/W/List) + 시크릿 읽기 +
  에셋 읽기(`asset.grantRead`) + `AmazonSSMManagedInstanceCore`.
- `userDataCausesReplacement: true` — 코드(에셋 해시)가 바뀌면 인스턴스를
  교체해 항상 깨끗한 부트스트랩. EIP는 유지되어 재연결되므로 CloudFront
  오리진 도메인은 불변(교체 중 수 분 다운타임 허용 — 데모 용도).

### 3.5 앱 배포 (CDK 에셋 + user-data)

`aws-s3-assets.Asset`으로 리포 루트를 zip (제외: `.git`, `infra`,
`**/node_modules`, `**/.venv`, `**/.next`, `**/cdk.out`, `**/__pycache__`,
`**/*.egg-info`, `**/test-results`, `**/playwright-report`, `docs`).
`backend/`, `frontend/`, `files/`(aiplc 룰)가 들어간다.

user-data 부트스트랩 순서:

1. `dnf install -y python3.11 nodejs20 nginx` (awscli2는 AL2023 기본 탑재).
2. 에셋 zip을 S3에서 받아 `/opt/pathfinder`에 전개.
3. 백엔드: `python3.11 -m venv .venv && pip install -e .`
4. 프론트: `npm ci && NEXT_PUBLIC_API_BASE_URL=/api npm run build`
5. 시크릿 조회 → `/etc/nginx/conf.d/pathfinder.conf` 생성(아래 §4).
6. systemd 유닛 2개 생성·기동:
   - `pathfinder-backend`: `uvicorn pathfinder.app:app --host 127.0.0.1 --port 8000`
     — env: `AWS_REGION`, `PATHFINDER_S3_REGION`, `PATHFINDER_S3_BUCKET`(prop으로
     받은 버킷), `ANTHROPIC_MODEL=global.anthropic.claude-opus-4-8`
   - `pathfinder-frontend`: `next start -H 127.0.0.1 -p 3000`
7. `systemctl enable --now nginx pathfinder-backend pathfinder-frontend`

첫 부팅 빌드에 ~5–10분 소요 — CFN 완료 직후 CloudFront가 잠시 502를
반환할 수 있음(정상, cfn-signal 대기는 넣지 않는다).

## 4. nginx — 헤더 검증 + 라우팅

```nginx
server {
  listen 80 default_server;

  if ($http_x_origin_verify != "<SECRET>") { return 403; }

  location /api/ {
    proxy_pass http://127.0.0.1:8000/;   # 트레일링 슬래시 = /api 프리픽스 제거
    proxy_http_version 1.1;
    proxy_buffering off;                  # SSE 즉시 전달
    proxy_read_timeout 3600s;
  }
  location / {
    proxy_pass http://127.0.0.1:3000;
  }
}
```

- 헤더 불일치(다른 CF 배포, 직접 스캔)는 무조건 403 — 앱까지 도달 못 함.
- SSE는 백엔드(sse-starlette)가 기본 15초 ping을 보내므로 CloudFront
  30초(→60초로 상향) 유휴 타임아웃에 걸리지 않는다.

## 5. CloudFront 배포

- 오리진: `HttpOrigin`, **HTTP_ONLY :80**, 도메인은 EIP에서 유도한
  퍼블릭 DNS(`ec2-<a-b-c-d>.<region>.compute.amazonaws.com` — `Fn::Join`/
  `Fn::Split`으로 IP의 점을 대시로 치환해 조립). EIP라 재배포에도 불변.
  (주의: us-east-1은 DNS 포맷이 `compute-1`이라 다름 — 필요 시 그때 분기.
  기본 리전 서울/도쿄에서는 위 포맷.)
- `readTimeout: 60초`, `customHeaders: { 'X-Origin-Verify': <secret> }`.
- 기본 비헤이비어: `ALLOW_ALL` 메서드, `CACHING_DISABLED`,
  `ALL_VIEWER` 오리진 리퀘스트 정책(헤더·쿠키·쿼리 전부 전달),
  `REDIRECT_TO_HTTPS`.
- 추가 비헤이비어 `/_next/static/*`: `CACHING_OPTIMIZED` (해시 파일명이라
  불변 캐시 안전).
- `PRICE_CLASS_200`(서울 엣지 포함), 기본 CloudFront 도메인 + 기본 인증서
  (커스텀 도메인은 범위 외).

## 6. 출력 (CfnOutputs)

- `DistributionDomain` — 접속 URL (`https://dxxxx.cloudfront.net`)
- `InstanceId` — SSM 접속용 (`aws ssm start-session --target <id>`)
- `EipAddress` — 오리진 IP (디버그용)

## 7. 테스트 / 검증

**인프라 테스트** (`infra/test/hosting-stack.test.ts`, 신규 — vitest 또는
jest 없이 기존 리포 관례에 맞춰 최소로):

- `cdk synth`가 통과한다 (프리픽스 리스트 컨텍스트는 `cdk.context.json`
  캐시로 재현).
- 어서션: SG 인바운드가 `SourcePrefixListId`만 갖고 0.0.0.0/0이 없다;
  CloudFront 오리진에 `OriginCustomHeaders`(X-Origin-Verify)가 있다;
  오리진 프로토콜이 `http-only`다; 포트 22 인그레스가 없다.

**배포 후 수동 검증**:

1. `curl -I http://<EIP>` → 타임아웃 (SG 차단).
2. `curl -I https://<dist>.cloudfront.net` → 200 (프론트 렌더).
3. 헤더 없이 오리진 IP로 직접 (SSM 인스턴스 내부에서
   `curl -H 'X-Origin-Verify: wrong' localhost`) → 403.
4. 프로젝트 생성 → 워크숍 턴 진행 — SSE 스트리밍이 CloudFront 경유로 동작.

## 8. 비용 (서울, 월 개략)

t4g.medium ~$30 + EIP ~$3.6 + Secrets Manager $0.40 + CloudFront 사용량
(데모 수준 ~$1 미만) ≈ **월 $35 내외**.

## 9. 범위 외

- 커스텀 도메인 / Route53 / ACM (기본 CloudFront 도메인 사용)
- ALB, Auto Scaling, 다중 AZ 고가용성
- CI/CD 파이프라인 (배포는 `npx cdk deploy`)
- 시크릿 자동 로테이션
- WAF
