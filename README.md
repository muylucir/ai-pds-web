# Pathfinder

AI-PLC Discovery 워크숍을 위한 대화형 캔버스. Strands 에이전트가 백엔드 프로세스 안에서
직접(in-process) Discovery 방법론을 구동하고, 프론트엔드가 그 턴을 실시간(SSE)으로 렌더한다.

```
frontend/  Next.js 15 (App Router) — 대시보드 · 질문 위저드 · 문서 리뷰 · 대화형 캔버스
backend/   FastAPI — 파서 · 인프로세스 Strands 에이전트 · SSE 턴 릴레이 · S3 영속화
infra/     CDK (TypeScript) — S3 버킷 + 백엔드 실행 롤 (서울, 리전 파라미터화)
```

---

## 사전 요구사항

- **Python 3.11** (백엔드 venv는 3.11로 생성 — 3.9로는 안 됨)
- **Node.js 20+** (프론트엔드; 검증 환경은 22)
- 서울(`ap-northeast-2`, 기본) 자격증명(호스트 롤/프로필) + Bedrock 접근 — 백엔드가 직접 Bedrock을 호출한다

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

인프라(`infra/`)는 S3 버킷 + 백엔드 실행 롤을 배포할 때만 필요하다 — 아래 "실행 방법" 절 참고.

---

## 실행 방법

프론트(:3000) → 백엔드(:8000) → 백엔드 프로세스 안에서 직접 도는 Strands 에이전트가
Bedrock을 호출해 응답한다. 백엔드 CORS가 `http://localhost:3000`을 기본 허용하고, 프론트는
기본 `http://localhost:8000`을 호출한다.

에이전트가 Bedrock을 호출하므로 AWS 자격증명(호스트 롤/프로필 — 인스턴스 프로파일이든 로컬
`~/.aws/credentials`든, S3 접근과 동일한 자격증명 체인)과 `PATHFINDER_S3_BUCKET`,
`ANTHROPIC_MODEL`이 필요하다. `backend/.env.example`을 `backend/.env`(gitignored)로 복사해
값을 채우면 기동 시 자동 로드된다(실 환경변수가 파일보다 우선). 값은 `cd infra && npx cdk
deploy` 출력(CfnOutputs)에서 가져온다 — 인프라 배포는 최초 1회만 필요하다:

```bash
cd infra
npm install
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2   # 계정·리전 최초 1회 (기본 서울)
npx cdk deploy --require-approval never
```

> 기본 배포 리전은 **서울(`ap-northeast-2`)**. 다른 리전에 배포하려면
> `CDK_DEPLOY_REGION=<region> npx cdk deploy`로 오버라이드한다(Bedrock 프로파일은
> 글로벌, IAM ARN은 리전 와일드카드라 리전만 바꾸면 그대로 동작).

배포가 끝나면 CfnOutputs를 출력한다 — 다음 단계 env로 쓴다:

```
ArtifactsBucketName → PATHFINDER_S3_BUCKET
BackendRoleArn      → 백엔드 프로세스가 이 롤(또는 동등한 정책)로 실행돼야 함
Region              → AWS_REGION / PATHFINDER_S3_REGION (기본 ap-northeast-2)
```

> ⚠️ 배포 리소스(S3 버킷 · IAM 롤)는 **비용이 발생**한다(스토리지 + 턴마다 Bedrock 호출).
> 다 쓰면 `npx cdk destroy`로 내린다.

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
캔버스에서 메시지를 보내면 실 에이전트가 Bedrock으로 응답한다.

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

## 환경 변수 요약

**백엔드**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PATHFINDER_CORS_ORIGINS` | `http://localhost:3000` | 콤마 구분 허용 origin |
| `PATHFINDER_S3_REGION` | `ap-northeast-2` | 영속 스토리지 리전(서울). **버킷이 만들어진 리전과 반드시 일치**시킬 것 |
| `PATHFINDER_S3_BUCKET` | — | 아티팩트 버킷 (CDK 출력) |
| `ANTHROPIC_MODEL` | — | Bedrock 추론 프로파일 id |
| `PATHFINDER_RULES_DIR` | `<repo>/files/aiplc-rules` | aiplc 룰 디렉토리(읽기 전용) |
| `PATHFINDER_WORKSPACES_DIR` | 시스템 tmp 하위 | 프로젝트별 로컬 워크스페이스 루트 |

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

# 프론트엔드 유닛 (Vitest + MSW; e2e는 제외)
cd frontend && npm test

# 인프라 합성 (배포 없이 템플릿 검증)
cd infra && npx cdk synth

# 프론트엔드 e2e (실 백엔드 + 실 Bedrock 자격증명 필요 — INTEGRATION)
cd frontend && npm run test:e2e
```

---

## 참고

- **인증은 아직 플레이스홀더**다(spec상 이후 단계). 라우트에 인증 없음, 프론트 `getAuthToken()`은
  `undefined` 반환. SSO/토큰 도입 시 `EventSource`가 커스텀 헤더를 못 보내므로 SSE 인증 전략
  (token-in-query 또는 cookie)을 함께 정해야 한다.
- **리전**: 전 리소스 서울(`ap-northeast-2`) 통일이 기본. 도쿄가 필요했던 이유는 Lambda
  MicroVMs가 도쿄에만 있어서였는데, 에이전트가 인프로세스로 바뀌며 그 제약이 사라졌다.
  다른 리전이 필요하면 `CDK_DEPLOY_REGION`(인프라)과 `AWS_REGION`/`PATHFINDER_S3_REGION`
  (백엔드)으로 지정한다 — 세 값이 같은 리전을 가리켜야 한다.
- 진행/결정 기록은 `.superpowers/sdd/progress.md`(git-ignored)와 `docs/superpowers/plans/` 참고.
