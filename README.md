# Pathfinder

AI-PLC Discovery 워크숍을 위한 대화형 캔버스. Claude Code 에이전트가 격리된 샌드박스에서
Discovery 방법론을 구동하고, 프론트엔드가 그 턴을 실시간(SSE)으로 렌더한다.

```
frontend/  Next.js 15 (App Router) — 대시보드 · 질문 위저드 · 문서 리뷰 · 대화형 캔버스
backend/   FastAPI — 파서 · 샌드박스 추상화(Local / MicroVM) · SSE 턴 릴레이
harness/   MicroVM 안에서 도는 하네스 서버 (Claude Code 드라이버 + /files + 라이프사이클 훅)
infra/     CDK (TypeScript) — Lambda MicroVMs 이미지 · IAM 롤 · S3 버킷 (도쿄)
```

샌드박스는 두 가지다:

| 모드 | 에이전트 | AWS 필요 | 용도 |
|---|---|---|---|
| **local** (기본) | 스크립트된 가짜 에이전트 | ❌ | UI·SSE·라우팅·에러 상태 개발/테스트 |
| **microvm** | 실 Claude Code + Bedrock Sonnet-5 | ✅ | 진짜 AI 턴 (풀스택) |

---

## 사전 요구사항

- **Python 3.11** (백엔드/하네스 venv는 3.11로 생성 — 3.9로는 안 됨)
- **Node.js 20+** (프론트엔드; 검증 환경은 22)
- microvm 모드에 한해: 도쿄(`ap-northeast-1`) 자격증명 + Bedrock Sonnet-5 접근, CDK 스택 배포

---

## 최초 1회 셋업

```bash
# 백엔드
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"      # 런타임 + 테스트 의존성

# 프론트엔드
cd ../frontend
npm install
```

하네스(`harness/`)와 인프라(`infra/`)는 microvm 모드에서만 필요하다 — 아래 "풀스택" 절 참고.

---

## 실행 방법 A — 로컬 모드 (AWS 없이, 권장 시작점)

프론트(:3000) → 백엔드(:8000) → `LocalSandbox`. 백엔드 CORS가 `http://localhost:3000`을
기본 허용하고, 프론트는 기본 `http://localhost:8000`을 호출한다.

> 이 모드의 에이전트 응답은 **스크립트된 가짜**다(실 Claude 아님). UI 흐름·SSE 스트리밍·
> 캔버스 카드·라우팅·에러 상태는 진짜로 검증되지만, AI 응답 내용은 아니다.

**터미널 1 — 백엔드:**
```bash
cd backend
.venv/bin/python -m uvicorn pathfinder.app:app --host 0.0.0.0 --port 8000 --reload
```

**터미널 2 — 프론트엔드:**
```bash
cd frontend
npm run dev            # http://localhost:3000
```

브라우저에서 `http://localhost:3000` → 프로젝트 생성 → 대시보드/질문/문서/캔버스.

### 브라우저가 원격(리버스 프록시 뒤)일 때

브라우저가 `localhost`가 아닌 프록시 호스트명으로 접속하면, 클라이언트의 `localhost:8000`
호출이 **브라우저 쪽 localhost**를 가리켜 `ERR_CONNECTION_REFUSED`가 난다. 이럴 땐 프론트가
**같은 오리진 `/api/*`** 로 호출하게 하고, Next의 route handler(`app/api/[...path]/route.ts`)가
서버사이드에서 백엔드로 프록시한다:

```bash
# frontend/.env.local
NEXT_PUBLIC_API_BASE_URL=/api
# (백엔드가 다른 호스트/포트면) PATHFINDER_BACKEND_URL=http://localhost:8000
```

그리고 프록시 origin의 dev cross-origin 경고를 없애려면 `next.config.mjs`의
`allowedDevOrigins`에 그 호스트명을 넣는다(이미 `frontend.workloom.net` 등록됨).

> route handler는 백엔드 응답의 hop-by-hop 헤더(`Connection` 등 — HTTP/2에서 금지)를 벗겨
> 재스트리밍한다. `next.config` `rewrites()`를 쓰지 않는 이유가 이것(그 프록시는 금지 헤더를
> 통과시켜 SSE가 브라우저에서 `ERR_HTTP2_PROTOCOL_ERROR`로 깨짐).
> **이 `/api` 프록시는 dev/데모 편의이며, 프로덕션은 프론트·백엔드 앞단의 실 리버스 프록시로 대체한다.**

---

## 실행 방법 B — 풀스택 (실 MicroVM + Bedrock, AWS 필요)

프론트 → 백엔드(`PATHFINDER_SANDBOX=microvm`) → 도쿄 Lambda MicroVM에서 실 Claude Code가
Bedrock Sonnet-5로 응답.

### B-1. 하네스 이미지 + 인프라 배포 (최초 1회, 코드 변경 시 재배포)

```bash
cd infra
npm install
./package-harness.sh                     # harness/ + files/aiplc-rules 를 빌드 컨텍스트로 스테이징
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-1   # 계정·리전 최초 1회
npx cdk deploy --require-approval never
```

배포가 끝나면 CfnOutputs를 출력한다 — 다음 단계 env로 쓴다:

```
ImageArn            → PATHFINDER_VM_IMAGE_ID
ExecutionRoleArn    → PATHFINDER_VM_ROLE_ARN
ArtifactsBucketName → PATHFINDER_S3_BUCKET
Region              → ap-northeast-1
```

> ⚠️ 배포 리소스(S3 버킷 · IAM 롤 · MicroVM 이미지)는 **비용이 발생**한다(이미지 버전 저장 +
> 턴마다 MicroVM 부팅 + Bedrock 호출). 다 쓰면 `npx cdk destroy`로 내린다.

### B-2. microvm 모드로 백엔드 기동

배포가 출력한 실제 값으로 채운다:

```bash
cd backend
export AWS_REGION=ap-northeast-1 AWS_DEFAULT_REGION=ap-northeast-1
export PATHFINDER_SANDBOX=microvm
export PATHFINDER_VM_REGION=ap-northeast-1
export PATHFINDER_S3_REGION=ap-northeast-1            # 드릴은 도쿄 통일; 코드 기본값은 서울(ap-northeast-2)
export PATHFINDER_VM_IMAGE_ID="arn:aws:lambda:ap-northeast-1:<ACCOUNT_ID>:microvm-image:pathfinder-harness"
export PATHFINDER_VM_ROLE_ARN="arn:aws:iam::<ACCOUNT_ID>:role/PathfinderDrillStack-ExecutionRole..."
export PATHFINDER_S3_BUCKET="pathfinderdrillstack-artifacts..."
export ANTHROPIC_MODEL="global.anthropic.claude-sonnet-5"
export PATHFINDER_CORS_ORIGINS="http://localhost:3000"   # 원격 프록시면 그 origin

.venv/bin/python -m uvicorn pathfinder.app:app --host 0.0.0.0 --port 8000
```

### B-3. 프론트엔드 기동

방법 A와 동일(`npm run dev` 또는 원격이면 `/api` 프록시 + `next build && npm start`).
캔버스에서 메시지를 보내면 실 MicroVM Claude가 응답한다.

---

## 환경 변수 요약

**백엔드**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PATHFINDER_SANDBOX` | (미설정=local) | `microvm`이면 실 MicroVM 경로 |
| `PATHFINDER_CORS_ORIGINS` | `http://localhost:3000` | 콤마 구분 허용 origin |
| `PATHFINDER_VM_REGION` | `ap-northeast-1` | MicroVM 리전 |
| `PATHFINDER_VM_IMAGE_ID` | — | 하네스 이미지 ARN (CDK 출력) |
| `PATHFINDER_VM_ROLE_ARN` | — | VM 실행 롤 ARN (CDK 출력) |
| `PATHFINDER_S3_REGION` | `ap-northeast-2` | 영속 스토리지 리전 (드릴은 도쿄로 오버라이드) |
| `PATHFINDER_S3_BUCKET` | — | 아티팩트 버킷 (CDK 출력) |
| `ANTHROPIC_MODEL` | — | Bedrock 추론 프로파일 id |

**프론트엔드**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | API base. 원격 프록시 뒤면 `/api` |
| `PATHFINDER_BACKEND_URL` | `http://localhost:8000` | `/api` route handler가 프록시할 백엔드 (server-side) |

---

## 테스트

```bash
# 백엔드 유닛 (AWS 불필요 — fake/Stubber)
cd backend && .venv/bin/python -m pytest -q

# 하네스 유닛 (AWS 불필요 — stub claude)
cd harness && .venv/bin/python -m pytest -q

# 프론트엔드 유닛 (Vitest + MSW; e2e는 제외)
cd frontend && npm test

# 인프라 합성 (배포 없이 템플릿 검증)
cd infra && npx cdk synth

# 프론트엔드 e2e (실 백엔드 필요 — INTEGRATION)
cd frontend && npm run test:e2e
```

---

## 참고

- **인증은 아직 플레이스홀더**다(spec상 이후 단계). 라우트에 인증 없음, 프론트 `getAuthToken()`은
  `undefined` 반환. SSO/토큰 도입 시 `EventSource`가 커스텀 헤더를 못 보내므로 SSE 인증 전략
  (token-in-query 또는 cookie)을 함께 정해야 한다.
- **리전**: 드릴은 전 리소스 도쿄 통일(합성 데이터). 실 워크숍 전에 프로덕션 리전(고객 문서
  국내 보관을 위한 서울 영속화 + 크로스보더 처리 공지 vs 도쿄 통일)을 재결정한다.
- 진행/결정 기록은 `.superpowers/sdd/progress.md`(git-ignored)와 `docs/superpowers/plans/` 참고.
```
