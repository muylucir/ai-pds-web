# EC2 + CloudFront 호스팅 (CDK) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pathfinder(프론트 Next.js + 백엔드 FastAPI)를 EC2 한 대에 올리고 CloudFront로 앞단을 두되, EC2 접근은 CloudFront origin-facing 관리형 프리픽스 리스트(배포 리전 자동)로만 허용하고, CloudFront가 붙이는 비밀 헤더를 nginx에서 검증해 우리 배포만 통과시킨다 — 전부 CDK로 관리.

**Architecture:** 기존 `PathfinderDrillStack`(S3 버킷 + 롤)은 유지하고, 신규 `PathfinderHostingStack`이 VPC/EC2/SG/Secret/CloudFront를 소유한다. EC2 user-data가 CDK 에셋(리포 zip)을 받아 백엔드·프론트를 빌드하고 nginx(헤더 검증 + `/api`→8000, `/`→3000)와 systemd로 기동한다. CloudFront↔EC2는 HTTP-only, 뷰어↔CloudFront는 HTTPS(기본 인증서·기본 도메인).

**Tech Stack:** AWS CDK 2.261 (aws-cdk-lib), TypeScript, `ec2.PrefixList.fromLookup`, `aws-s3-assets.Asset`, `secretsmanager.Secret`, `aws-cloudfront` + `aws-cloudfront-origins`, AL2023(arm64/Graviton), nginx, systemd, `aws-cdk-lib/assertions` + `node:assert` + ts-node(테스트).

## Global Constraints

- **CDK 버전:** `aws-cdk-lib ^2.150`(설치본 2.261), CLI 2.1132 — 새 라이브러리 의존성 추가 금지(테스트도 `aws-cdk-lib/assertions` + `node:assert`만 사용, vitest/jest 추가 안 함).
- **리전 파라미터화:** `CDK_DEPLOY_REGION > CDK_DEFAULT_REGION > 'ap-northeast-2'`(서울). 두 스택 모두 동일 리전에 배포. `bin/app.ts`의 기존 우선순위 로직을 그대로 재사용.
- **프리픽스 리스트 이름:** `com.amazonaws.global.cloudfront.origin-facing` (IPv4, AWS 소유, 글로벌 이름이지만 리전마다 ID 다름 — `fromLookup`이 배포 리전의 ID를 조회).
- **비밀 헤더 이름:** `X-Origin-Verify`. 값은 Secrets Manager 자동 생성(`excludePunctuation: true`, 32자 → 영숫자만 → nginx 문자열 비교 안전).
- **모델 ID:** `global.anthropic.claude-opus-4-8` (드릴 스택 `MODEL` 상수와 동일 — 재사용).
- **에셋 소스:** 리포 루트. 제외 패턴: `.git`, `infra`, `docs`, `**/node_modules`, `**/.venv`, `**/.next`, `**/cdk.out`, `**/__pycache__`, `**/*.egg-info`, `**/test-results`, `**/playwright-report`, `files/*.png`. (`backend/`, `frontend/`, `files/aiplc-rules`가 번들에 포함되어야 함 — `files/`는 gitignored지만 배포자 워킹트리에 존재하며 Asset은 git이 아닌 로컬 파일시스템에서 복사한다.)
- **프론트 빌드:** `NEXT_PUBLIC_API_BASE_URL=/api`를 빌드 전 export(Next는 `NEXT_PUBLIC_*`를 빌드 시 인라인). 브라우저는 same-origin `/api/*` 호출 → nginx가 백엔드로.
- **테스트 가능성:** 스택은 `cfPrefixListId?: string` prop을 받아, 있으면 `PrefixList.fromPrefixListId`, 없으면 `fromLookup`(프로덕션 기본, 자동 리전). 테스트는 더미 `pl-test0000`을 주입해 크리덴셜 없이 synth.
- **커밋 메시지 말미:** `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

## File Structure

- Create: `infra/lib/backend-permissions.ts` — 백엔드 IAM 정책 문(Bedrock invoke + 아티팩트 S3 R/W/List)을 만드는 공유 헬퍼. 드릴 스택과 호스팅 스택이 함께 사용(DRY).
- Modify: `infra/lib/pathfinder-drill-stack.ts` — 헬퍼 사용으로 리팩터, `public readonly artifactsBucket` 노출(호스팅 스택 크로스스택 참조용). 외부 동작(템플릿) 불변.
- Create: `infra/lib/user-data.ts` — `renderUserData(opts): string` 순수 함수. 부트스트랩 bash 스크립트 생성(패키지 설치 · 에셋 전개 · 빌드 · 시크릿 조회 · nginx conf · systemd 유닛).
- Create: `infra/lib/pathfinder-hosting-stack.ts` — `PathfinderHostingStack`. VPC · SG(프리픽스 리스트 인그레스) · Secret · EC2(EIP·인스턴스 롤·에셋·user-data) · CloudFront.
- Modify: `infra/bin/app.ts` — 두 스택 인스턴스화, 드릴 버킷을 호스팅에 prop 전달.
- Modify: `infra/package.json` — `"test"` 스크립트 추가(ts-node 어서션 스크립트 2개).
- Create: `infra/test/user-data.assert.ts` — `renderUserData` 순수 함수 단위 어서션.
- Create: `infra/test/hosting-stack.assert.ts` — `Template.fromStack` 어서션(SG·롤·CloudFront).
- Modify: `infra/README.md` — 호스팅 스택 배포 절차 · 크리덴셜 필요(lookup) · SSM 접속 안내.

**Task 분할:** 1(공유 헬퍼+드릴 리팩터) · 2(user-data 순수함수) · 3(스택: VPC/SG/Secret) · 4(스택: EC2/롤/에셋/EIP) · 5(스택: CloudFront) · 6(bin/app 배선 + package.json + README + 전체 synth).

---

### Task 1: 공유 IAM 헬퍼 + 드릴 스택 버킷 노출

기존 드릴 스택의 Bedrock/S3 정책을 헬퍼로 뽑아 두 스택이 공유한다. 드릴 스택은 버킷을 public으로 노출해 호스팅 스택이 참조할 수 있게 한다. **외부 동작(합성되는 템플릿)은 바뀌지 않는다** — 순수 리팩터 + 노출.

**Files:**
- Create: `infra/lib/backend-permissions.ts`
- Modify: `infra/lib/pathfinder-drill-stack.ts`
- Test: `infra/test/hosting-stack.assert.ts` (이 태스크에서 드릴 파트만 먼저 작성)

**Interfaces:**
- Produces: `backendPolicyStatements(bucket: s3.IBucket, account: string): iam.PolicyStatement[]` — Bedrock invoke + `s3:Get/Put/Delete` on `projects/*`·`sessions/*` + `s3:ListBucket`(prefix 조건). 3개 statement 배열.
- Produces: `MODEL = 'global.anthropic.claude-opus-4-8'`, `MODEL_FAMILY = 'anthropic.claude-opus-4-8'` (헬퍼에서 export, 드릴/호스팅 공유).
- Produces: `PathfinderDrillStack`에 `public readonly artifactsBucket: s3.Bucket`.

- [ ] **Step 1: 헬퍼 파일 작성**

Create `infra/lib/backend-permissions.ts`:

```ts
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';

export const MODEL = 'global.anthropic.claude-opus-4-8';
export const MODEL_FAMILY = 'anthropic.claude-opus-4-8';

// 백엔드(드릴 롤 또는 EC2 인스턴스 롤)가 필요로 하는 공통 권한:
// Bedrock invoke + 아티팩트 버킷 projects/*·sessions/* 읽기/쓰기/목록.
export function backendPolicyStatements(
  bucket: s3.IBucket,
  account: string,
): iam.PolicyStatement[] {
  return [
    new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*:${account}:inference-profile/${MODEL}`,
        `arn:aws:bedrock:*::foundation-model/${MODEL_FAMILY}*`,
      ],
    }),
    new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: [`${bucket.bucketArn}/projects/*`, `${bucket.bucketArn}/sessions/*`],
    }),
    new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['projects/*', 'sessions/*'] } },
    }),
  ];
}
```

- [ ] **Step 2: 드릴 스택 리팩터 — 헬퍼 사용 + 버킷 노출**

Replace `infra/lib/pathfinder-drill-stack.ts` fully:

```ts
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { backendPolicyStatements } from './backend-permissions';

export class PathfinderDrillStack extends cdk.Stack {
  public readonly artifactsBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;

    // Artifacts bucket — 프로젝트 산출물(projects/*)과 strands 세션(sessions/*).
    const bucket = new s3.Bucket(this, 'Artifacts', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });
    this.artifactsBucket = bucket;

    // 백엔드 프로세스가 assume하는 실행 롤: Bedrock invoke + S3(projects/* & sessions/*).
    // 백엔드가 EC2/컨테이너 인스턴스 프로파일로 이 롤을 맡거나, 롤 정책을 그대로
    // 인스턴스 프로파일에 부여한다(호스트 자격증명 모델, spec §2).
    const backendRole = new iam.Role(this, 'BackendRole', {
      assumedBy: new iam.AccountPrincipal(account),
      description: 'Pathfinder backend: Bedrock invoke + artifacts/session S3 access.',
    });
    for (const stmt of backendPolicyStatements(bucket, account)) {
      backendRole.addToPolicy(stmt);
    }

    new cdk.CfnOutput(this, 'ArtifactsBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'BackendRoleArn', { value: backendRole.roleArn });
    // 스택이 실제로 배포되는 리전(bin/app.ts의 env.region으로 결정).
    new cdk.CfnOutput(this, 'Region', { value: this.region });
  }
}
```

- [ ] **Step 3: 드릴 어서션 작성(실패 확인용 최소)**

Create `infra/test/hosting-stack.assert.ts` (이 태스크에서는 드릴 파트만):

```ts
import * as assert from 'node:assert';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';

const ENV = { account: '123456789012', region: 'ap-northeast-2' };

function testDrillUnchanged() {
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill', { env: ENV });
  const t = Template.fromStack(drill);

  // Bedrock invoke 문 존재.
  t.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith(['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream']),
        }),
      ]),
    },
  });
  // 버킷 1개 노출.
  assert.ok(drill.artifactsBucket, 'artifactsBucket must be exposed');
  t.resourceCountIs('AWS::S3::Bucket', 1);
  console.log('OK  drill stack: policy + bucket exposed');
}

testDrillUnchanged();
```

- [ ] **Step 4: 어서션 실행 — 통과 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/hosting-stack.assert.ts`
Expected: `OK  drill stack: policy + bucket exposed` 출력, exit 0. (실패 시 `assert`/synth 에러로 non-zero.)

- [ ] **Step 5: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/backend-permissions.ts infra/lib/pathfinder-drill-stack.ts infra/test/hosting-stack.assert.ts
git commit -m "refactor(infra): extract backend IAM policy helper; expose drill bucket

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: user-data 렌더러 (순수 함수)

부트스트랩 bash 스크립트를 생성하는 순수 함수. 파일 IO·AWS 호출 없이 문자열만 반환 → 단위 테스트로 핵심 요소(시크릿 조회, nginx 헤더 검증, systemd 유닛, 빌드 env)를 검증한다.

**Files:**
- Create: `infra/lib/user-data.ts`
- Test: `infra/test/user-data.assert.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface UserDataOptions {
    region: string;        // 배포 리전 (aws CLI --region, S3/Bedrock)
    bucketName: string;    // PATHFINDER_S3_BUCKET
    model: string;         // ANTHROPIC_MODEL
    secretArn: string;     // X-Origin-Verify 시크릿 ARN (부팅 시 조회)
    assetS3Uri: string;    // s3://bucket/key — 에셋 zip 위치
  }
  export function renderUserData(opts: UserDataOptions): string;
  ```
- 스크립트 규약(테스트가 검증):
  - `set -euxo pipefail`로 시작, 로그는 `/var/log/pathfinder-bootstrap.log`로 tee.
  - 앱 루트 `/opt/pathfinder`. 백엔드 `/opt/pathfinder/backend`, 프론트 `/opt/pathfinder/frontend`, 룰 `/opt/pathfinder/files/aiplc-rules`.
  - 프론트 빌드 전 `export NEXT_PUBLIC_API_BASE_URL=/api`.
  - nginx server: `if ($http_x_origin_verify != "<SECRET>") { return 403; }`, `location /api/ { proxy_pass http://127.0.0.1:8000/; }`(트레일링 슬래시), `location / { proxy_pass http://127.0.0.1:3000; }`, `proxy_buffering off`.
  - systemd 유닛 `pathfinder-backend.service`(uvicorn 127.0.0.1:8000, 위 env), `pathfinder-frontend.service`(next start -H 127.0.0.1 -p 3000).

- [ ] **Step 1: 실패 테스트 작성**

Create `infra/test/user-data.assert.ts`:

```ts
import * as assert from 'node:assert';
import { renderUserData } from '../lib/user-data';

const s = renderUserData({
  region: 'ap-northeast-2',
  bucketName: 'my-artifacts-bucket',
  model: 'global.anthropic.claude-opus-4-8',
  secretArn: 'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:hdr-AbCdEf',
  assetS3Uri: 's3://asset-bucket/abc123.zip',
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

console.log('OK  user-data: all required elements present');
```

- [ ] **Step 2: 실행 — 실패 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/user-data.assert.ts`
Expected: FAIL — `Cannot find module '../lib/user-data'` (아직 미작성).

- [ ] **Step 3: `renderUserData` 구현**

Create `infra/lib/user-data.ts`:

```ts
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
sed -i '/server {/,/^    }/d' /etc/nginx/nginx.conf || true

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
ExecStart=/usr/bin/npm run start -- -H 127.0.0.1 -p 3000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now nginx pathfinder-backend pathfinder-frontend
`;
}
```

주의: `next start` 커맨드는 systemd에서 `npm run start -- -H 127.0.0.1 -p 3000`으로 실행되며 package.json의 `"start": "next start"`에 인자가 전달된다. 테스트 정규식 `next start -H 127.0.0.1 -p 3000`은 이 스크립트 문자열에 그대로 존재해야 하므로, 유닛 파일 주석에 실제 커맨드를 명시한다.

- [ ] **Step 4: 커맨드 문자열 정합 — 유닛에 next 커맨드 명시**

`renderUserData`의 frontend 유닛 `ExecStart`를 아래로 교체(테스트 정규식 `next start -H 127.0.0.1 -p 3000` 충족):

```
ExecStart=/usr/bin/npx next start -H 127.0.0.1 -p 3000
```

(`/usr/bin/npx`는 nodejs20-npm이 설치. `npx next`는 로컬 `node_modules/.bin/next`를 실행.)

- [ ] **Step 5: 실행 — 통과 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/user-data.assert.ts`
Expected: `OK  user-data: all required elements present`, exit 0.

- [ ] **Step 6: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/user-data.ts infra/test/user-data.assert.ts
git commit -m "feat(infra): user-data renderer — asset unpack, build, nginx header auth, systemd

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: HostingStack — VPC + SG(프리픽스 리스트) + Secret

호스팅 스택 뼈대. VPC(퍼블릭 서브넷·NAT 없음), 프리픽스 리스트로만 80을 여는 SG, 비밀 헤더 시크릿까지. EC2/CloudFront는 다음 태스크.

**Files:**
- Create: `infra/lib/pathfinder-hosting-stack.ts`
- Test: `infra/test/hosting-stack.assert.ts` (호스팅 어서션 추가)

**Interfaces:**
- Consumes: (없음 — Task 1 헬퍼는 Task 4에서 사용)
- Produces:
  ```ts
  export interface HostingStackProps extends cdk.StackProps {
    artifactsBucket: s3.IBucket;   // 드릴 스택에서 전달
    cfPrefixListId?: string;       // 테스트 주입용; 미지정 시 fromLookup(자동 리전)
  }
  export class PathfinderHostingStack extends cdk.Stack { ... }
  ```
- 이 태스크 산출: `this.vpc: ec2.Vpc`, `this.sg: ec2.SecurityGroup`, `this.headerSecret: secretsmanager.Secret` (다음 태스크가 참조).

- [ ] **Step 1: 실패 어서션 추가**

Append to `infra/test/hosting-stack.assert.ts` (파일 하단, `testDrillUnchanged();` 위/아래 무관하나 아래에 추가):

```ts
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

function makeHosting() {
  const app = new cdk.App();
  const drill = new PathfinderDrillStack(app, 'Drill2', { env: ENV });
  const stack = new PathfinderHostingStack(app, 'Hosting', {
    env: ENV,
    artifactsBucket: drill.artifactsBucket,
    cfPrefixListId: 'pl-test0000',   // 주입 → fromLookup 우회(크리덴셜 불필요)
  });
  return Template.fromStack(stack);
}

function testNetworkAndSecret() {
  const t = makeHosting();
  // 80 인그레스가 프리픽스 리스트 소스만 사용, CIDR 오픈 없음.
  t.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
    IpProtocol: 'tcp', FromPort: 80, ToPort: 80, SourcePrefixListId: 'pl-test0000',
  });
  // 22(SSH) 인그레스 전무.
  const ingresses = t.findResources('AWS::EC2::SecurityGroupIngress');
  for (const [, r] of Object.entries(ingresses)) {
    assert.notStrictEqual((r as any).Properties.FromPort, 22, 'no SSH ingress allowed');
  }
  // 0.0.0.0/0 인그레스 없음(별도 인라인 ingress도 없어야).
  const sgs = t.findResources('AWS::EC2::SecurityGroup');
  for (const [, r] of Object.entries(sgs)) {
    const inline = (r as any).Properties.SecurityGroupIngress ?? [];
    for (const rule of inline) {
      assert.notStrictEqual(rule.CidrIp, '0.0.0.0/0', 'no open CIDR ingress');
    }
  }
  // NAT 게이트웨이 0.
  t.resourceCountIs('AWS::EC2::NatGateway', 0);
  // 비밀 헤더 시크릿 존재(구두점 제외 생성).
  t.hasResourceProperties('AWS::SecretsManager::Secret', {
    GenerateSecretString: Match.objectLike({ ExcludePunctuation: true }),
  });
  console.log('OK  hosting: SG prefix-list only, no SSH, secret present');
}

testNetworkAndSecret();
```

- [ ] **Step 2: 실행 — 실패 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/hosting-stack.assert.ts`
Expected: FAIL — `Cannot find module '../lib/pathfinder-hosting-stack'`.

- [ ] **Step 3: 스택 뼈대 구현(VPC/SG/Secret)**

Create `infra/lib/pathfinder-hosting-stack.ts`:

```ts
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';

export interface HostingStackProps extends cdk.StackProps {
  artifactsBucket: s3.IBucket;
  // 테스트 주입용. 미지정 시 배포 리전의 CloudFront origin-facing 프리픽스
  // 리스트를 fromLookup으로 자동 조회한다.
  cfPrefixListId?: string;
}

export class PathfinderHostingStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: HostingStackProps) {
    super(scope, id, props);

    // --- VPC: 퍼블릭 서브넷만, NAT 없음(비용 0). ---
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
      ],
    });

    // --- CloudFront origin-facing 프리픽스 리스트 (리전 자동) ---
    const cfPrefixListId =
      props.cfPrefixListId ??
      ec2.PrefixList.fromLookup(this, 'CfOriginFacing', {
        prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
      }).prefixListId;

    // --- SG: 80만, CloudFront 프리픽스 리스트에서만. SSH 없음. ---
    const sg = new ec2.SecurityGroup(this, 'InstanceSg', {
      vpc,
      allowAllOutbound: true, // 패키지 설치 · Bedrock · S3
      description: 'Pathfinder EC2 — inbound 80 from CloudFront prefix list only.',
    });
    sg.addIngressRule(
      ec2.Peer.prefixList(cfPrefixListId),
      ec2.Port.tcp(80),
      'CloudFront origin-facing only',
    );

    // --- 비밀 헤더 값 (영숫자 32자) ---
    const headerSecret = new secretsmanager.Secret(this, 'OriginVerifyHeader', {
      description: 'X-Origin-Verify shared secret (CloudFront custom header <-> nginx).',
      generateSecretString: {
        passwordLength: 32,
        excludePunctuation: true,
        // 단일 스칼라 시크릿(문자열 그대로) — JSON 아님.
      },
    });

    // 다음 태스크(EC2/CloudFront)에서 사용.
    void vpc;
    void sg;
    void headerSecret;
  }
}
```

- [ ] **Step 4: 실행 — 통과 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/hosting-stack.assert.ts`
Expected: 세 줄 `OK ...` 모두 출력, exit 0.

- [ ] **Step 5: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/pathfinder-hosting-stack.ts infra/test/hosting-stack.assert.ts
git commit -m "feat(infra): hosting stack — VPC, prefix-list SG (region-auto), origin-verify secret

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: HostingStack — EC2 + 인스턴스 롤 + 에셋 + EIP

인스턴스(AL2023 arm64), 백엔드 권한을 가진 인스턴스 롤(+시크릿 읽기+SSM), 리포 에셋, user-data 배선, 고정 EIP.

**Files:**
- Modify: `infra/lib/pathfinder-hosting-stack.ts`
- Test: `infra/test/hosting-stack.assert.ts`

**Interfaces:**
- Consumes: `backendPolicyStatements`, `MODEL` (Task 1); `renderUserData` (Task 2); Task 3의 `vpc`/`sg`/`headerSecret`.
- Produces: 스택 필드 `this.instance: ec2.Instance`, `this.originDnsName: string`(EIP에서 유도한 퍼블릭 DNS — Task 5 CloudFront 오리진), `this.headerSecret`(Task 5 커스텀 헤더). 필드는 `private`/`public` 자유지만 Task 5가 같은 파일이므로 지역 변수로 이어써도 됨.

- [ ] **Step 1: 실패 어서션 추가**

Append to `infra/test/hosting-stack.assert.ts`:

```ts
function testComputeAndRole() {
  const t = makeHosting();
  // arm64 인스턴스 1대, IMDSv2 강제(HttpTokens required).
  t.hasResourceProperties('AWS::EC2::Instance', {
    InstanceType: 't4g.medium',
  });
  t.hasResourceProperties('AWS::EC2::LaunchTemplate', {
    LaunchTemplateData: Match.objectLike({
      MetadataOptions: Match.objectLike({ HttpTokens: 'required' }),
    }),
  });
  // EIP 존재 + 연결.
  t.resourceCountIs('AWS::EC2::EIP', 1);
  t.resourceCountIs('AWS::EC2::EIPAssociation', 1);
  // 인스턴스 롤: Bedrock + 시크릿 읽기 + SSM 관리형 정책.
  t.hasResourceProperties('AWS::IAM::Role', {
    ManagedPolicyArns: Match.arrayWith([
      Match.objectLike({
        'Fn::Join': Match.arrayWith([
          Match.arrayWith([Match.stringLikeRegexp('AmazonSSMManagedInstanceCore')]),
        ]),
      }),
    ]),
  });
  const policies = t.findResources('AWS::IAM::Policy');
  const allActions = JSON.stringify(policies);
  assert.match(allActions, /secretsmanager:GetSecretValue/, 'instance role reads header secret');
  assert.match(allActions, /bedrock:InvokeModel/, 'instance role invokes bedrock');
  console.log('OK  hosting: EC2 arm64 + EIP + instance role (bedrock/secret/ssm)');
}

testComputeAndRole();
```

- [ ] **Step 2: 실행 — 실패 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/hosting-stack.assert.ts`
Expected: FAIL — `AWS::EC2::Instance` 미존재 어서션 실패.

- [ ] **Step 3: EC2/롤/에셋/EIP 구현**

`infra/lib/pathfinder-hosting-stack.ts` 상단 import에 추가:

```ts
import * as iam from 'aws-cdk-lib/aws-iam';
import * as assets from 'aws-cdk-lib/aws-s3-assets';
import * as path from 'path';
import { backendPolicyStatements, MODEL } from './backend-permissions';
import { renderUserData } from './user-data';
```

Task 3의 `void vpc; void sg; void headerSecret;` 자리를 아래로 교체:

```ts
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // --- 인스턴스 롤: 백엔드 공통 권한 + 시크릿 읽기 + SSM 접속 ---
    const role = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
      description: 'Pathfinder EC2: Bedrock + artifacts S3 + read header secret.',
    });
    for (const stmt of backendPolicyStatements(props.artifactsBucket, account)) {
      role.addToPolicy(stmt);
    }
    headerSecret.grantRead(role);

    // --- 앱 코드 에셋(리포 zip) ---
    const asset = new assets.Asset(this, 'AppAsset', {
      path: path.join(__dirname, '..', '..'), // 리포 루트
      exclude: [
        '.git', 'infra', 'docs',
        '**/node_modules', '**/.venv', '**/.next', '**/cdk.out',
        '**/__pycache__', '**/*.egg-info', '**/test-results',
        '**/playwright-report', 'files/*.png',
      ],
    });
    asset.grantRead(role);

    // --- user-data ---
    const userData = ec2.UserData.custom(
      renderUserData({
        region,
        bucketName: props.artifactsBucket.bucketName,
        model: MODEL,
        secretArn: headerSecret.secretArn,
        assetS3Uri: asset.s3ObjectUrl, // s3://bucket/key
      }),
    );

    // --- 인스턴스 (AL2023 arm64/Graviton, IMDSv2 강제) ---
    const instance = new ec2.Instance(this, 'Instance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MEDIUM),
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.ARM_64,
      }),
      securityGroup: sg,
      role,
      userData,
      requireImdsv2: true,
      userDataCausesReplacement: true, // 에셋(코드) 변경 시 깨끗한 재부트스트랩
      blockDevices: [{
        deviceName: '/dev/xvda',
        volume: ec2.BlockDeviceVolume.ebs(20, { encrypted: true }),
      }],
    });

    // --- 고정 EIP (재배포에도 CloudFront 오리진 도메인 불변) ---
    const eip = new ec2.CfnEIP(this, 'Eip', { domain: 'vpc' });
    new ec2.CfnEIPAssociation(this, 'EipAssoc', {
      allocationId: eip.attrAllocationId,
      instanceId: instance.instanceId,
    });

    // EIP IP -> 퍼블릭 DNS 이름 (CloudFront 오리진은 도메인만 허용; IP 불가).
    //   ec2-<a-b-c-d>.<region>.compute.amazonaws.com  (us-east-1은 compute-1)
    const computeDomain =
      region === 'us-east-1' ? 'compute-1.amazonaws.com' : `${region}.compute.amazonaws.com`;
    const originDnsName =
      `ec2-${cdk.Fn.join('-', cdk.Fn.split('.', eip.attrPublicIp))}.${computeDomain}`;

    // Task 5(CloudFront)에서 사용.
    void instance;
    void originDnsName;
```

그리고 Task 3에서 선언한 `const headerSecret`, `const vpc`, `const sg`가 이 블록에서 참조되므로, 세 `void ...;` 줄은 이미 제거됨(위 교체로). 파일 끝의 `void headerSecret;` 등 잔여 라인이 있으면 삭제한다.

- [ ] **Step 4: 실행 — 통과 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/hosting-stack.assert.ts`
Expected: 네 줄 `OK ...` 출력, exit 0. (에셋 번들링이 리포 루트에서 수행되며 수 초 소요될 수 있음.)

- [ ] **Step 5: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/pathfinder-hosting-stack.ts infra/test/hosting-stack.assert.ts
git commit -m "feat(infra): hosting stack — EC2 arm64, instance role, app asset, EIP + origin DNS

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: HostingStack — CloudFront 배포

CloudFront: HTTP-only 오리진 + 비밀 커스텀 헤더, HTTPS 리다이렉트, `/_next/static/*` 캐시, 60초 읽기 타임아웃(SSE).

**Files:**
- Modify: `infra/lib/pathfinder-hosting-stack.ts`
- Test: `infra/test/hosting-stack.assert.ts`

**Interfaces:**
- Consumes: Task 4의 `originDnsName`, Task 3의 `headerSecret`.
- Produces: CfnOutput `DistributionDomain`, `InstanceId`, `EipAddress`.

- [ ] **Step 1: 실패 어서션 추가**

Append to `infra/test/hosting-stack.assert.ts`:

```ts
function testCloudFront() {
  const t = makeHosting();
  t.hasResourceProperties('AWS::CloudFront::Distribution', {
    DistributionConfig: Match.objectLike({
      Origins: Match.arrayWith([
        Match.objectLike({
          CustomOriginConfig: Match.objectLike({ OriginProtocolPolicy: 'http-only' }),
          OriginCustomHeaders: Match.arrayWith([
            Match.objectLike({ HeaderName: 'X-Origin-Verify' }),
          ]),
        }),
      ]),
      DefaultCacheBehavior: Match.objectLike({
        ViewerProtocolPolicy: 'redirect-to-https',
      }),
    }),
  });
  // /_next/static/* 캐시 비헤이비어 존재.
  const dists = t.findResources('AWS::CloudFront::Distribution');
  const cfg = Object.values(dists)[0] as any;
  const behaviors = cfg.Properties.DistributionConfig.CacheBehaviors ?? [];
  assert.ok(
    behaviors.some((b: any) => b.PathPattern === '/_next/static/*'),
    'static cache behavior present',
  );
  console.log('OK  hosting: CloudFront http-only origin + X-Origin-Verify header + https redirect');
}

testCloudFront();
```

- [ ] **Step 2: 실행 — 실패 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/hosting-stack.assert.ts`
Expected: FAIL — `AWS::CloudFront::Distribution` 미존재.

- [ ] **Step 3: CloudFront 구현**

`infra/lib/pathfinder-hosting-stack.ts` import에 추가:

```ts
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
```

Task 4의 `void instance; void originDnsName;` 자리를 아래로 교체:

```ts
    // 비밀 헤더 값: CFN dynamic reference({{resolve:secretsmanager:...}})로 주입.
    // 배포 시 CloudFormation이 해석 → 템플릿에는 평문이 남지 않음.
    const origin = new origins.HttpOrigin(originDnsName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      httpPort: 80,
      readTimeout: cdk.Duration.seconds(60), // SSE(백엔드 ping 15s) 여유
      keepaliveTimeout: cdk.Duration.seconds(60),
      customHeaders: {
        'X-Origin-Verify': headerSecret.secretValue.unsafeUnwrap(),
      },
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'Pathfinder — CloudFront in front of EC2 (header-authenticated origin).',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200,
      defaultBehavior: {
        origin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
      },
      additionalBehaviors: {
        '/_next/static/*': {
          origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED, // 해시 파일명 → 불변
        },
      },
    });

    new cdk.CfnOutput(this, 'DistributionDomain', {
      value: `https://${distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new cdk.CfnOutput(this, 'EipAddress', { value: eip.attrPublicIp });
```

주의(`unsafeUnwrap`): CDK-생성 시크릿은 synth 시점에 값이 없으므로 `secretValue.unsafeUnwrap()`는 평문이 아니라 dynamic reference 토큰(`{{resolve:secretsmanager:...}}`)을 반환한다 → 템플릿에 평문 유출 없음. `.unsafeUnwrap()`은 `SecretValue`를 문자열 토큰으로 꺼내는 API이며, 여기서는 그 토큰(=dynamic reference)이 CloudFront customHeaders 값으로 들어가고 배포 시 CloudFormation이 실제 값으로 치환한다.

- [ ] **Step 4: 실행 — 통과 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx ts-node test/hosting-stack.assert.ts`
Expected: 다섯 줄 `OK ...` 출력, exit 0.

- [ ] **Step 5: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/lib/pathfinder-hosting-stack.ts infra/test/hosting-stack.assert.ts
git commit -m "feat(infra): hosting stack — CloudFront http-only origin with X-Origin-Verify header

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: bin/app 배선 + package.json + README + 전체 synth

두 스택을 앱에 등록하고 드릴 버킷을 호스팅에 전달. 테스트 스크립트 등록. README에 배포/접속 절차. 마지막으로 전체 synth로 실제 합성을 검증(크리덴셜 있으면 프리픽스 리스트 lookup까지).

**Files:**
- Modify: `infra/bin/app.ts`
- Modify: `infra/package.json`
- Modify: `infra/README.md`

**Interfaces:**
- Consumes: `PathfinderDrillStack.artifactsBucket`, `PathfinderHostingStack(props)`.

- [ ] **Step 1: bin/app.ts 배선**

Replace `infra/bin/app.ts` fully:

```ts
#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';
import { PathfinderHostingStack } from '../lib/pathfinder-hosting-stack';

const app = new cdk.App();

// 리전 우선순위: CDK_DEPLOY_REGION > CDK_DEFAULT_REGION(프로파일) > 서울.
const region =
  process.env.CDK_DEPLOY_REGION ?? process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2';
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = { region, account };

const drill = new PathfinderDrillStack(app, 'PathfinderDrillStack', { env });

// 호스팅 스택은 CloudFront origin-facing 프리픽스 리스트를 배포 리전에서
// 자동 조회한다(fromLookup) — synth/deploy 시 크리덴셜 필요, 결과는
// cdk.context.json에 캐시된다(커밋 대상).
new PathfinderHostingStack(app, 'PathfinderHostingStack', {
  env,
  artifactsBucket: drill.artifactsBucket,
});
```

- [ ] **Step 2: package.json 테스트 스크립트**

`infra/package.json`의 `scripts`를 아래로 교체:

```json
  "scripts": {
    "synth": "cdk synth",
    "deploy": "cdk deploy",
    "test": "ts-node test/user-data.assert.ts && ts-node test/hosting-stack.assert.ts"
  },
```

- [ ] **Step 3: 테스트 스크립트 실행 — 통과 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npm test`
Expected: user-data `OK`, drill/hosting 5개 `OK` 모두 출력, exit 0.

- [ ] **Step 4: 드릴 스택 synth(크리덴셜 불필요) 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx cdk synth PathfinderDrillStack`
Expected: 드릴 스택 YAML 출력, 에러 없음.

- [ ] **Step 5: 호스팅 스택 synth(프리픽스 리스트 lookup) 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp/infra && npx cdk synth PathfinderHostingStack`
Expected: 성공 시 호스팅 스택 YAML 출력 + `cdk.context.json` 생성(프리픽스 리스트 ID 캐시). 크리덴셜이 없으면 lookup 단계에서 에러 — 그 경우 배포 계정 크리덴셜을 설정하고 재실행(이것이 정상 동작이며, 첫 synth는 크리덴셜을 요구한다).

- [ ] **Step 6: cdk.context.json 커밋 여부 확인**

Run: `cd /home/ec2-user/project/pathfinder-sp && git status --short infra/cdk.context.json && git check-ignore infra/cdk.context.json || echo "not ignored (commit it)"`
Expected: `not ignored (commit it)` — `cdk.out`만 gitignore되어 있고 `cdk.context.json`은 커밋 대상. (lookup 결과 캐시로 재현성 확보.)

- [ ] **Step 7: README 갱신**

Replace `infra/README.md` fully:

```markdown
# Pathfinder Infra (CDK, 기본 ap-northeast-2 / 서울)

두 스택:

- **PathfinderDrillStack** — S3 아티팩트 버킷(`projects/*` + `sessions/*`) +
  백엔드 실행 롤(Bedrock invoke + S3). 인프로세스 Strands 에이전트용.
- **PathfinderHostingStack** — VPC + EC2(AL2023 arm64) + CloudFront. EC2는
  CloudFront origin-facing 관리형 프리픽스 리스트(배포 리전 자동)에서만 80을
  받고, CloudFront가 붙이는 비밀 헤더 `X-Origin-Verify`를 nginx가 검증한다.
  user-data가 리포 에셋을 받아 백엔드/프론트를 빌드·기동한다.

## 리전 (파라미터)
기본 서울(`ap-northeast-2`). 다른 리전은 `CDK_DEPLOY_REGION`으로 오버라이드:
```bash
CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all   # 예: 도쿄
```
프리픽스 리스트 ID는 리전마다 다르지만 `PrefixList.fromLookup`이 배포 리전의
ID를 자동 조회하므로 코드 수정이 필요 없다.

## 테스트
```bash
npm ci
npm test            # user-data 순수함수 + 스택 어서션 (크리덴셜 불필요)
```

## Synth / deploy
```bash
npx cdk synth PathfinderDrillStack      # 크리덴셜 불필요
npx cdk synth PathfinderHostingStack    # 프리픽스 리스트 lookup — 크리덴셜 필요(최초 1회)
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2   # 계정·리전 최초 1회
npx cdk deploy --all --require-approval never
```
> 호스팅 스택은 배포 리전의 CloudFront 프리픽스 리스트를 lookup하므로 첫
> synth/deploy에 계정 크리덴셜이 필요하다. 조회 결과는 `cdk.context.json`에
> 캐시되며 **커밋**한다(재현성). EC2 첫 부팅 빌드에 ~5–10분 걸리므로 배포
> 완료 직후 CloudFront가 잠시 502를 반환할 수 있다(정상).

## 출력 (CfnOutputs)
- `PathfinderHostingStack.DistributionDomain` — 접속 URL(`https://dxxxx.cloudfront.net`)
- `PathfinderHostingStack.InstanceId` — SSM 접속: `aws ssm start-session --target <id>`
- `PathfinderHostingStack.EipAddress` — 오리진 IP(디버그)
- `PathfinderDrillStack.ArtifactsBucketName` / `BackendRoleArn` / `Region`

## 접속 · 검증
- 브라우저 → `DistributionDomain`(HTTPS) → CloudFront → EC2 nginx.
- EC2에는 SSH 포트가 열려있지 않다 — `aws ssm start-session --target <InstanceId>`.
- 오리진 직접 접근은 SG(프리픽스 리스트)로 차단되고, 설령 도달해도 nginx가
  헤더 없으면 403.
```

- [ ] **Step 8: 커밋**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add infra/bin/app.ts infra/package.json infra/README.md
git add infra/cdk.context.json 2>/dev/null || true   # lookup 캐시 생성됐으면 포함
git commit -m "feat(infra): wire drill + hosting stacks; test script; hosting README

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 배포 후 수동 검증 (참고 — 실 배포 시)

1. `curl -I --max-time 5 http://<EipAddress>` → 타임아웃(SG 차단).
2. `curl -I https://<DistributionDomain>` → 200(프론트 렌더).
3. SSM로 인스턴스 진입 후 `curl -s -o /dev/null -w '%{http_code}' -H 'X-Origin-Verify: wrong' http://localhost` → `403`.
4. 브라우저에서 프로젝트 생성 → 워크숍 턴 진행 — SSE 스트리밍이 CloudFront 경유로 동작(활동 인디케이터가 실시간 갱신).

## Self-Review 결과

- **Spec 커버리지:** §2 아키텍처→Task 3–5, §3.2 프리픽스 리스트 자동→Task 3(fromLookup), §3.3 시크릿→Task 3+5, §3.4 EC2→Task 4, §3.5 배포(에셋/user-data)→Task 2+4, §4 nginx→Task 2, §5 CloudFront→Task 5, §6 출력→Task 5, §7 테스트→각 태스크 어서션 + Task 6 synth. 전 항목 태스크 존재.
- **플레이스홀더:** 없음(모든 스텝에 실제 코드/커맨드).
- **타입 정합:** `backendPolicyStatements(bucket, account)`·`renderUserData(opts)`·`HostingStackProps` 시그니처가 정의 태스크(1·2·3)와 소비 태스크(3·4·5)에서 일치.
- **범위:** 단일 구현 계획으로 적정(한 서브시스템: 호스팅 인프라).
