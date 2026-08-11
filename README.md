# Pathfinder

AI-PLC Discovery 워크숍용 대화형 캔버스.

Claude Agent SDK 에이전트가 백엔드 프로세스 안에서 Discovery 방법론을 구동하고,
프론트엔드가 그 턴을 SSE로 실시간 렌더한다. Discovery가 만든 프로토타입 스펙은 같은
화면에서 실물 앱으로 빌드·호스팅되고, 무인증 토큰 링크로 공유하는 검증 설문까지
이어진다. 화면과 생성물 모두 한국어·영어를 지원한다.

```
frontend/  Next.js 15 (App Router) — 대시보드 · 워크스페이스 · 문서 리뷰 · 프로토타입
backend/   FastAPI — Discovery 에이전트 · SSE 릴레이 · S3 영속화 · 프로토타입 빌드/호스팅 · JWT 검증
infra/     CDK (TypeScript) — S3 + 백엔드 롤 + Cognito + EC2/CloudFront (서울 기본)
```

- 동작 방식과 설계 판단의 근거: [`docs/design-notes.md`](docs/design-notes.md)
- 스택 내부 구조(콜백 URL 순환 의존, 오리진 보호 등): [`infra/README.md`](infra/README.md)

---

## CDK로 배포하기

한 번의 `cdk deploy --all`로 접속 가능한 앱이 뜬다 — 인프라만 만드는 게 아니라 EC2가
리포를 받아 백엔드·프론트를 빌드·기동하고 CloudFront가 그 앞에 붙는다.

| 스택 | 만드는 것 |
|---|---|
| `PathfinderDrillStack` | S3 아티팩트 버킷(`projects/*` + `sessions/*` + `surveys/*` + `models/*`) + 백엔드 실행 롤(Bedrock invoke + S3) |
| `PathfinderAuthStack` | Cognito User Pool + Hosted UI v2 + 역할 그룹(`admin`/`pm`) + 시드 계정 2개 |
| `PathfinderHostingStack` | VPC + EC2(AL2023 x86_64, m7i.2xlarge, EBS 100GB 암호화) + CloudFront |

세 스택은 서로 의존하므로 **`--all`로 함께 배포**한다(`app.ts`가 버킷·User Pool 참조를
호스팅 스택에 넘긴다). 배포 순서는 CDK가 정한다.

### 1. 사전 준비

- Node.js 20+
- AWS 자격증명(프로파일 또는 인스턴스 롤) — 관리자급 권한이 필요하다(IAM 롤·Cognito·VPC 생성)
- **Bedrock 모델 액세스 활성화** — 배포 리전 콘솔에서 사용할 Claude 모델을 켜 둔다.
  이걸 빼먹으면 배포는 성공하고 첫 대화 턴에서 `AccessDeniedException`이 난다.

### 2. 부트스트랩

```bash
cd infra
npm ci
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2   # 계정·리전 조합당 최초 1회
```

### 3. 배포

```bash
npm test                              # (선택) 스택 어서션 — 크리덴셜 불필요
npx cdk diff --all                    # (선택) 기존 배포와의 차이
npx cdk deploy --all --require-approval never
```

`--require-approval never`가 필요한 이유: 세 스택 모두 IAM/보안 그룹을 만들어 매번 승인
프롬프트가 뜬다. 무인 배포가 아니면 이 플래그를 빼고 직접 확인해도 된다.

**소요 시간은 15~20분**이다. CloudFront 배포와 EC2 첫 부팅 빌드(백엔드 venv + 프론트
`next build`)가 대부분을 차지한다. `cdk deploy`가 끝난 직후에도 EC2 빌드가 진행 중일 수
있어 **CloudFront가 몇 분간 502를 반환하는 것은 정상**이다.

> ⚠️ **배포되는 것은 커밋된 코드가 아니라 현재 워킹 트리다.** 호스팅 스택은 리포 루트를
> zip 에셋으로 올린다 — 제외 목록은 `infra/app-asset-excludes.json`이고 `.gitignore`와는
> **별개**다. 미커밋 변경도 그대로 배포되므로 배포 전 `git status`로 확인한다. 새로
> gitignore한 로컬 산출물은 이 목록에도 넣어야 한다(실제로 `proto-type/`이 빠져 있어서,
> 개발 박스에서 만든 프로토타입이 배포 zip에 실려 새 인스턴스에서 "빌드 완료"로 보였다).
>
> 목록의 **`.claude/`** 는 이유가 다르다. 에이전트의 cwd가 `/opt/pathfinder/workspaces/{pid}`
> 이므로 `/opt/pathfinder/.claude/`는 **조상**이 되고, Claude Code는 cwd에서 위로 올라가며
> `CLAUDE.md`를 전부 로드한다 — 개발용 설정의 한국어 한 줄이 영어 프로젝트의 컨텍스트에 매
> 턴 들어갔다(실측). `CLAUDE_CONFIG_DIR`은 조상 탐색을 막지 못하므로 **에셋에서 빼는 것이
> 유일한 차단이다.** `infra/test`가 이 목록을 단정한다.

### 4. 출력값

```
PathfinderHostingStack.DistributionDomain → 접속 URL (https://dxxxx.cloudfront.net)
PathfinderHostingStack.InstanceId         → aws ssm start-session --target <id>
PathfinderDrillStack.ArtifactsBucketName  → PATHFINDER_S3_BUCKET
PathfinderDrillStack.BackendRoleArn       → 백엔드가 이 롤(또는 동등 정책)로 실행돼야 함
PathfinderDrillStack.Region               → AWS_REGION / PATHFINDER_S3_REGION
PathfinderAuthStack.UserPoolId            → PATHFINDER_COGNITO_USER_POOL_ID
PathfinderAuthStack.UserPoolClientId      → PATHFINDER_COGNITO_CLIENT_ID / COGNITO_CLIENT_ID
PathfinderAuthStack.HostedUiDomain        → COGNITO_HOSTED_UI_DOMAIN
```

EC2 배포에서는 user-data가 이 값들을 자동으로 백엔드/프론트 env에 넣는다 — 손으로 설정할
필요가 없다. 위 매핑은 **로컬 개발에서 같은 인프라를 쓸 때** 참고한다.

### 5. 접속

`DistributionDomain`으로 접속해 시드 계정으로 로그인한다:

| 계정 | 역할 | 비밀번호 |
|---|---|---|
| `admin@pathfinder.local` | 관리자 (사용자 관리 가능) | `PathFinder2026!@` |
| `pm@pathfinder.local` | PM | `PathFinder2026!@` |

> ⚠️ **이 비밀번호는 데모/워크숍용이다.** CDK 소스의 상수이므로 CloudFormation 템플릿과
> 스택 이벤트에 평문으로 남고, 재배포하면 이 값으로 되돌아간다. 실제 운영에 쓰려면
> `infra/lib/auth-client-config.ts`의 `SEED_PASSWORD`를 교체하고, 시드 계정 대신
> `/admin/users`에서 초대한 계정을 쓴다.

### 리전 변경

기본은 **서울(`ap-northeast-2`)**. 다른 리전은 환경변수로 오버라이드한다:

```bash
CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all --require-approval never
```

코드 수정은 필요 없다 — Bedrock 추론 프로파일은 글로벌이고, IAM ARN은 리전 와일드카드이며,
CloudFront 프리픽스 리스트는 `PrefixList.fromLookup`이 배포 리전에서 자동 조회한다(조회
결과는 로컬 `infra/cdk.context.json`에 캐시된다 — 계정 ID가 키에 들어가는 캐시라 커밋하지
않고, 크리덴셜이 있으면 synth/deploy가 다시 조회해 재생성한다).

### 코드만 다시 배포하기

```bash
cd infra && npx cdk deploy PathfinderHostingStack --require-approval never
```

에셋 해시가 바뀌면 user-data가 갱신되고 EC2가 교체된다. 급한 수정이라면 SSM으로 들어가
직접 갱신하는 게 빠르다: `aws ssm start-session --target <InstanceId>`.

### 삭제

```bash
cd infra && npx cdk destroy --all
```

> ⚠️ 배포 리소스(S3 · IAM · Cognito · EC2/CloudFront)는 **비용이 발생**한다(스토리지 + 턴마다
> Bedrock 호출 + EC2 상시 가동). 워크숍이 끝나면 내린다.
>
> ⚠️ User Pool은 `RemovalPolicy.DESTROY`이므로 **사용자 계정이 전원 함께 사라진다.** S3
> 아티팩트 버킷에 남기고 싶은 산출물이 있으면 먼저 내려받는다.

### 트러블슈팅

**먼저 백엔드 로그를 본다.** 대부분의 증상이 화면에서는 빈 화면이나 일반적인 실패로만
보이고, 원인은 여기에만 남는다:

```bash
aws ssm start-session --target <InstanceId>
sudo journalctl -u pathfinder-backend -f            # 실시간
sudo journalctl -u pathfinder-backend --since -1h | grep -v '/proto/'   # 프리뷰 프록시 소음 제거
```

| 증상 | 원인 / 대처 |
|---|---|
| 배포 직후 CloudFront 502 | EC2 첫 빌드가 진행 중(5~10분). SSM으로 `sudo tail -f /var/log/cloud-init-output.log` |
| 스택이 `ROLLBACK_COMPLETE`라 재배포 거부 | **최초 생성이 실패한 스택은 업데이트가 불가능하다.** 먼저 내린 뒤 다시 배포한다: `npx cdk destroy PathfinderAuthStack` → `npx cdk deploy --all`. `UPDATE_ROLLBACK_COMPLETE`(기존 스택의 업데이트 실패)는 그냥 재배포하면 된다 |
| 첫 대화 턴에서 `AccessDeniedException` | 배포 리전에 그 모델의 **Bedrock 모델 액세스**가 꺼져 있다. IAM은 `global.anthropic.claude-*`를 전부 허용하므로 IAM이 원인일 가능성은 낮다 |
| 특정 기능만 500이고 화면에는 원인이 안 보임 | 대개 IAM이다. 백엔드 로그의 `AccessDenied`가 어떤 액션·리소스인지 말해 준다 |
| 로그인 후 `redirect_mismatch` | 호스팅 스택의 콜백 URL 등록(`UpdateUserPoolClient`)이 실패. `cdk deploy PathfinderHostingStack` 재실행 |
| `cdk synth`가 크리덴셜을 요구 | 호스팅 스택의 프리픽스 리스트 lookup. 결과가 로컬 `cdk.context.json`(gitignored)에 캐시되므로 클론당 최초 1회만 필요하다 |
| SSH 접속 불가 | 의도된 설계다. SSH 포트가 없고 SSM만 열려 있다 |
| 프로토타입 프리뷰가 404 | **의도된 응답이다** — 접근 토큰 쿠키가 없거나 다른 프로토타입의 것이다. 공유 링크(`/api/proto/t/{token}`)로 들어가야 쿠키가 심긴다. 자세한 분기는 `docs/design-notes.md`의 "인증과 프로토타입 접근 토큰" |
| 영어 프로젝트인데 문서·채팅이 한국어로 나옴 | 언어 지시가 두 레벨에서 충돌한 것이고 **이 실패는 에러를 내지 않는다.** `docs/design-notes.md`의 "언어" 참고 |
| 영어 UI인데 일부 문구만 한국어 | 딕셔너리를 안 타고 소스에 박힌 리터럴이다. `cd frontend && npm test -- noHardcodedKorean`이 위치를 집어 준다 |
| 워크스페이스 채팅 내역이 빈 목록 | `list_history`가 모든 실패를 `[]`로 강등한다. `projects/{pid}/discovery/transcript/`에 객체가 있는지부터 확인한다 — `docs/design-notes.md`의 "트랜스크립트 미러링" |
| 긴 메시지를 보내면 "연결이 끊어졌습니다" | 요청 라인이 Node `maxHeaderSize`를 넘은 것(HTTP 431). `docs/design-notes.md`의 "긴 채팅 입력" |

---

## 로컬 개발 실행

프론트(:3000) → 백엔드(:8000) → 백엔드 안에서 도는 Discovery 에이전트가 Bedrock을 호출한다.
버킷·롤은 필요하므로 `npx cdk deploy PathfinderDrillStack`만 배포하면 된다.

**사전 요구사항**: Python **3.11**(3.9로는 안 됨), Node.js 20+, Bedrock 접근 자격증명.

```bash
# 최초 1회
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && npm install
cp ../backend/.env.example ../backend/.env      # 값은 위 CfnOutputs에서 가져온다

# 터미널 1 — 백엔드
cd backend && .venv/bin/python -m uvicorn pathfinder.app:app --host 0.0.0.0 --port 8000 --reload

# 터미널 2 — 프론트엔드
cd frontend && npm run dev            # http://localhost:3000
```

`http://localhost:3000` → 프로젝트 생성(모델과 **생성물 언어**를 여기서 고른다) → 대시보드 /
워크스페이스 / 문서 리뷰 / 프로토타입.

### 브라우저가 원격(리버스 프록시 뒤)일 때

브라우저가 `localhost`가 아닌 프록시 호스트명으로 접속하면 클라이언트의 `localhost:8000`
호출이 **브라우저 쪽 localhost**를 가리켜 `ERR_CONNECTION_REFUSED`가 난다. 프론트가 같은
오리진 `/api/*`로 호출하게 하면 Next route handler(`app/api/[...path]/route.ts`)가 서버사이드
에서 백엔드로 프록시한다:

```bash
# frontend/.env.local
NEXT_PUBLIC_API_BASE_URL=/api
# (백엔드가 다른 호스트/포트면) PATHFINDER_BACKEND_URL=http://localhost:8000
```

dev cross-origin 경고를 없애려면 `next.config.mjs`의 `allowedDevOrigins`에 그 호스트명을
넣는다. **이 `/api` 프록시는 dev/데모 편의이며, 프로덕션은 실 리버스 프록시로 대체한다.**

---

## 환경 변수

EC2 배포는 user-data가 전부 채운다. 아래는 **로컬에서 손으로 설정하는 것들**이고, 전체
목록은 [`docs/design-notes.md`](docs/design-notes.md#환경-변수-전체-목록)에 있다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PATHFINDER_S3_BUCKET` | — | 아티팩트 버킷(CDK 출력) |
| `PATHFINDER_S3_REGION` | `ap-northeast-2` | 영속 스토리지 리전. **버킷이 만들어진 리전과 일치**시킬 것 |
| `ANTHROPIC_MODEL` | — (EC2는 `global.anthropic.claude-opus-4-8`) | **폴백** Bedrock 추론 프로파일 id. 프로젝트가 자기 모델을 가지면 그것이 이긴다 |
| `PATHFINDER_CORS_ORIGINS` | `http://localhost:3000` | 콤마 구분 허용 origin |
| `PATHFINDER_LOG_LEVEL` | `INFO` | 애플리케이션 로그 레벨(`app.configure_logging()`) |
| `PATHFINDER_COGNITO_USER_POOL_ID` / `_CLIENT_ID` | — | **둘 다 비우면** 인증 전체 바이패스(로컬 기본). 하나만 비우면 모든 요청이 RuntimeError(fail-closed) |
| `PATHFINDER_COOKIE_SECURE` | `false` (EC2는 `true`) | 프로토타입 접근 쿠키에 `Secure`를 붙일지. 로컬은 끈 채로 둔다 |
| `PATHFINDER_PUBLIC_PATH_PREFIX` | `/api` | **브라우저가 보는** 프리뷰 마운트. 백엔드를 :8000으로 직접 부르는 로컬은 `""` |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | 프론트가 부를 API base. 원격 프록시 뒤면 `/api` |
| `COGNITO_HOSTED_UI_DOMAIN` / `COGNITO_CLIENT_ID` / `COGNITO_CLIENT_SECRET` | — | 프론트 server-side 전용. 시크릿에 **`NEXT_PUBLIC_` 금지** |

---

## 테스트

```bash
cd backend && .venv/bin/python -m pytest -q     # 백엔드 유닛 (AWS 불필요)
cd frontend && npm test                         # 프론트 유닛 (Vitest + MSW)
cd infra && npm test                            # 인프라 합성 + 템플릿 단정 (배포 없이)
cd frontend && npm run test:e2e                 # e2e (실 백엔드 + 실 Bedrock 필요)
```
