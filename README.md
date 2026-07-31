# Pathfinder

AI-PLC Discovery 워크숍을 위한 대화형 캔버스. Claude Agent SDK 기반 에이전트가 백엔드
프로세스 안에서 직접(in-process) Discovery 방법론을 구동하고, 프론트엔드가 그 턴을
실시간(SSE)으로 렌더한다. (구 Strands 드라이버는 `PATHFINDER_DISCOVERY_DRIVER=strands`
폴백으로 남아 있다.) 워크스페이스 채팅도 프로토타입 빌드와 같은 입력창 ■ 버튼으로 진행 중인
턴을 중단할 수 있다(`POST /projects/{pid}/interrupt`) — 지금까지 한 작업은 그대로 남는다.

Discovery가 산출한 프로토타입 스펙(`PROTOTYPE-{slug}.md`)은 프론트 **"프로토타입" 탭**에서
바로 실물로 이어진다: 세션을 시작하면 백엔드 프로세스 안에서 직접 도는 Claude Agent SDK
빌드 에이전트가 그 스펙을 읽고 대화형으로 앱을 빌드하며(진행 중 질문은 기존 질문 위저드로
왕복, 중단 버튼 지원), 완료되면 Pathfinder EC2가 같은 빌드 디렉토리를 그대로(in-place)
로컬 프로세스로 호스팅해 `/api/proto/{projectId}/{slug}/` 경로 프록시로 라이브 프리뷰를
제공한다. 빌드 트랜스크립트는 S3로 미러링되므로 세션을 닫거나 백엔드가 재시작돼도 맥락이
사라지지 않고, 나중에 다시 시작하면(`resume`) 이전 대화를 이어받는다.

빌드 세션의 수명은 **빌드 1회**다: 에이전트가 완성물을 만들고 `build_complete`
도구로 완료를 선언하면 세션이 스스로 닫혀 서브프로세스와 빌드 슬롯을 즉시
반납한다. 그 시점에 빌드 드로어는 완료 카드로 바뀌어 호스팅 시작·개선 이어서
하기·닫기로 분기한다. "개선"은 전체 트랜스크립트를 다시 싣지 않고 새 세션에
이전 빌드 요약(`handoff.json`)만 주입한다 — 버튼 색 하나 바꾸는 요청이 빌드
전체 맥락을 지고 가지 않게 한다. 완료 선언 없이 세션이 죽으면(유휴 타임아웃,
백엔드 재시작) 종전처럼 트랜스크립트를 `resume`한다.

세션 상태(S3)와 빌드 산출물(EC2 로컬 디스크)은 수명이 다르다. **프로토타입
리셋이나 인스턴스 교체 뒤에는 기록만 남고 `prototype/`이 비어 있을 수 있다** —
그때 개시 프롬프트는 이어서 하라는 지시 대신 "이전 코드를 찾지 말고 스펙을 다시
읽어 처음부터 다시 만들라"로 갈린다(`proto/session.py`의 `has_build_output`).
알려주지 않으면 에이전트가 트랜스크립트를 믿고 삭제된 코드를 파일시스템에서
찾아 헤맨다.

Discovery 대화도 같은 방식으로 S3에 미러링된다(`projects/{pid}/discovery/transcript/`,
`agent/session_store.py`). CLI는 트랜스크립트를 로컬 디스크에만 두므로 이것이 없으면
인스턴스 교체와 함께 대화가 사라지고, 워크스페이스를 다시 열었을 때 복원할 것이 없다.
여기에 두 가지 제약이 붙어 있고 둘 다 실측으로 확인된 것이다:

- **flush 시점** — SDK 기본값 `batched`는 `result` 메시지나 `close()`에서만 쓴다.
  Discovery는 질문이 뜨면 그 자리에서 턴을 끝내고(`questions` → `done`) 클라이언트를
  프로세스 수명 내내 캐시하므로 둘 중 어느 것에도 닿지 않는다. 그래서
  `session_store_flush="eager"`로 두고, 턴을 마감하기 직전 배처를 직접 flush한다
  (`claude_driver._flush_transcript_mirror`).
- **세션 키** — CLI가 비-UUID `--session-id`를 거부하므로 드라이버는 project_id에서
  uuid5를 유도해(`_sdk_session_id`) 그 값으로 미러링한다. 읽는 쪽도 같은 유도를 거쳐야
  한다(`load_transcript`가 안에서 변환한다) — project_id를 그대로 프리픽스에 넣으면
  아무것도 쓰이지 않은 곳을 뒤지고, `list_history`의 실패 강등이 그것을 빈 목록으로
  삼킨다.

복원은 **라이브 스트림과 같은 모양**을 만드는 것이 목표다(`session_history.transform_cli_transcript`).
CLI는 도구를 부를 때마다 별도 assistant 줄을 쓰므로 줄마다 항목을 만들면 말풍선 하나였던
턴이 여러 개로 쪼개지고 대부분이 빈 말풍선이 된다 — 턴 경계는 **실제 사용자 발화**이고
(`tool_result`만 담은 user 줄은 도구 실행 결과이지 발화가 아니다) 그 안의 assistant 줄은
하나로 누적한다.

자세한 배포 절차는 `infra/README.md`, 수동 e2e 검증은
`docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md` 참고.

프로토타입을 사용자에게 검증할 때는 같은 탭에서 **검증 설문**을 만들 수 있다.
프로토타입 명세의 검증 가설·기능 목록에서 문항을 생성하고(`validation-questionnaire.md`),
인증이 필요 없는 토큰 링크(`/survey/{token}`)를 공유해 익명 응답을 받고, 집계를
대시보드로 확인한 뒤 CSV로 내보내 Discovery의 검증 종합 단계에 넣는다. 응답은 S3에
저장되며(응답 1건 = 객체 1개), 대시보드는 `rollup.json` 캐시를 읽는다.

문항은 **응답자가 본 것이 데모라는 전제** 위에서 만들어진다(`survey/builder.py`의
`QUESTIONNAIRE_PROMPT`). 성능·보안·실데이터 정확도·도입 시점은 묻지 않고 — 룰이 그
단계에서 의도적으로 만들지 않는 것들이므로(`prototype-validation.md` Step 3의
"NOT production code") 물어서 받은 낮은 점수는 판단에 쓸 수 없다 — 대신 "실제 업무에
도입된다면 이 방향이 맞는가"를 가정형으로 묻는다. 기능 choice 문항에는 "사용하지
않았다/해당 없음" 선택지가 들어간다: 룰의 Feature Validation 표에 "Not tested — Users
did not reach this feature" 행이 있고, 그 선택지가 없으면 응답자가 써 보지 않은 기능을
추측으로 평가해 집계가 신호와 잡음을 구별할 수 없다. 공용 설문 화면의 안내문도 같은
전제를 말한다 — 두 곳이 어긋나면 응답자가 목 데이터를 실제 결과로 오해한다.

설문 데이터는 대부분 프로젝트 프리픽스 아래(`projects/{pid}/prototypes/{slug}/survey/`)
있지만, **토큰 인덱스만 버킷 루트의 `surveys/by-token/`에 있다** — 공개 링크는 토큰이
어느 프로젝트 것인지 알기 전에 이걸 읽어야 하므로 프로젝트 프리픽스 안에 둘 수 없다.
백엔드 IAM 정책이 이 세 번째 프리픽스를 덮어야 한다
(`infra/lib/backend-permissions.ts`의 `BACKEND_BUCKET_PREFIXES`). 실제로 빠져 있어서
설문 생성이 전부 500이었고, 원인은 백엔드 로그의 `AccessDenied`에만 남았다.

```
frontend/  Next.js 15 (App Router) — 대시보드 · 워크스페이스 · 문서 리뷰 · 프로토타입 탭 · 로그인/사용자 관리
           (상단 네비는 이 4개다. `projects/[projectId]/canvas`·`/questions`는
            워크스페이스로 대체된 구 화면이 남아 있는 것 — 네비에 노출되지 않는다)
backend/   FastAPI — 파서 · 인프로세스 Discovery 에이전트(Claude Agent SDK) · SSE 턴 릴레이 · S3 영속화 · 프로토타입 빌드/호스팅 · JWT 검증
infra/     CDK (TypeScript) — S3 버킷 + 백엔드 실행 롤 + Cognito(Hosted UI v2) + EC2/CloudFront (서울, 리전 파라미터화)
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

인프라(`infra/`)는 AWS에 배포할 때 필요하다 — 아래 "CDK로 배포하기" 절 참고. 로컬
개발만 할 거면 S3 버킷을 만드는 `PathfinderDrillStack`만 배포하면 된다.

---

## CDK로 배포하기 (전체 스택)

한 번의 `cdk deploy --all`로 접속 가능한 앱이 뜬다 — 인프라만 만드는 게 아니라 EC2가
리포를 받아 백엔드·프론트를 빌드·기동하고 CloudFront가 그 앞에 붙는다. 로컬 개발만 할
거라면 이 절을 건너뛰고 아래 "로컬 개발 실행"으로 가도 된다(단 S3 버킷은 필요하므로
`PathfinderDrillStack`만 배포한다).

### 스택 구성

| 스택 | 만드는 것 |
|---|---|
| `PathfinderDrillStack` | S3 아티팩트 버킷(`projects/*` + `sessions/*` + `surveys/*`) + 백엔드 실행 롤(Bedrock invoke + S3) |
| `PathfinderAuthStack` | Cognito User Pool + Hosted UI v2 + 역할 그룹(`admin`/`pm`) + 시드 계정 2개 |
| `PathfinderHostingStack` | VPC + EC2(AL2023 x86_64, m7i.2xlarge, EBS 100GB 암호화) + CloudFront |

세 스택은 서로 의존하므로 **`--all`로 함께 배포**한다(`app.ts`가 버킷·User Pool 참조를
호스팅 스택에 넘긴다). 배포 순서는 CDK가 정한다.

### 0. 사전 준비

- AWS 자격증명(프로파일 또는 인스턴스 롤) — 관리자급 권한이 필요하다(IAM 롤·Cognito·VPC 생성)
- **Bedrock 모델 액세스 활성화** — 배포 리전 콘솔에서 사용할 Claude 모델을 켜 둔다.
  이걸 빼먹으면 배포는 성공하고 첫 대화 턴에서 `AccessDeniedException`이 난다.
- Node.js 20+

### 1. 의존성 설치 + 부트스트랩

```bash
cd infra
npm ci
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2   # 계정·리전 조합당 최초 1회
```

### 2. 배포 전 검증 (선택, 권장)

```bash
npm test                              # user-data 순수함수 + 스택 어서션 (크리덴셜 불필요)
npx cdk diff --all                    # 기존 배포와의 차이
```

### 3. 배포

```bash
npx cdk deploy --all --require-approval never
```

`--require-approval never`가 필요한 이유: 세 스택 모두 IAM/보안 그룹을 만들어 매번
승인 프롬프트가 뜬다. 무인 배포가 아니면 이 플래그를 빼고 직접 확인해도 된다.

**소요 시간은 15~20분**이다. CloudFront 배포와 EC2 첫 부팅 빌드(백엔드 venv + 프론트
`next build`)가 대부분을 차지한다. `cdk deploy`가 끝난 직후에도 EC2 빌드가 진행 중일 수
있어 **CloudFront가 몇 분간 502를 반환하는 것은 정상**이다.

> ⚠️ **배포되는 것은 커밋된 코드가 아니라 현재 워킹 트리다.** 호스팅 스택은 리포
> 루트를 zip 에셋으로 올린다(`.git`, `infra`, `docs`, `node_modules`, `.venv`,
> `.next`, `cdk.out`, `__pycache__`, `*.egg-info`, `test-results`,
> `playwright-report`, `files/*.png`, `.env*`, 그리고 빌드 에이전트의 런타임
> 산출물(`proto-type/`·`protos/`·`*-config/projects/`) 제외 —
> `lib/pathfinder-hosting-stack.ts`). 미커밋 변경도 그대로 배포되므로, 배포 전
> `git status`로 의도한 상태인지 확인한다. `.gitignore`와는 **별개 목록**이므로
> gitignored라고 자동 제외되지는 않는다 — 새로 gitignore한 로컬 산출물은 이
> 목록에도 넣어야 한다. (실제로 `proto-type/`이 빠져 있어서, 개발 박스에서 만든
> 프로토타입이 배포 zip에 실려 새 인스턴스에서 "빌드 완료"로 보였다.)

### 4. 출력값 확인

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

EC2 배포에서는 user-data가 이 값들을 자동으로 백엔드/프론트 env에 넣는다 — 손으로
설정할 필요가 없다. 위 매핑은 **로컬 개발에서 같은 인프라를 쓸 때** 참고한다.

### 5. 접속

`DistributionDomain`으로 접속해 시드 계정으로 로그인한다:

| 계정 | 역할 | 비밀번호 |
|---|---|---|
| `admin@pathfinder.local` | 관리자 (사용자 관리 가능) | `PathFinder2026!@` |
| `pm@pathfinder.local` | PM | `PathFinder2026!@` |

> ⚠️ **이 비밀번호는 데모/워크숍용이다.** CDK 소스의 상수이므로 CloudFormation
> 템플릿과 스택 이벤트에 평문으로 남고, 계정에 CFN 읽기 권한이 있는 사람은 누구나
> 볼 수 있다. 재배포하면 이 값으로 되돌아간다. 실제 운영에 쓰려면
> `infra/lib/auth-client-config.ts`의 `SEED_PASSWORD`를 교체하고, 시드 계정 대신
> `/admin/users`에서 초대한 계정을 쓴다.

### 리전 변경

기본은 **서울(`ap-northeast-2`)**. 다른 리전은 환경변수로 오버라이드한다:

```bash
CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all --require-approval never
```

코드 수정은 필요 없다 — Bedrock 추론 프로파일은 글로벌이고, IAM ARN은 리전
와일드카드이며, CloudFront 프리픽스 리스트는 `PrefixList.fromLookup`이 배포 리전에서
자동 조회한다(조회 결과는 `infra/cdk.context.json`에 캐시되며 커밋 대상이다).

### 코드만 다시 배포하기

앱 코드를 고친 뒤 인프라는 그대로 두고 재배포할 때:

```bash
cd infra && npx cdk deploy PathfinderHostingStack --require-approval never
```

에셋 해시가 바뀌면 user-data가 갱신되고 EC2가 교체된다. 급한 수정이라면 SSM으로 들어가
직접 갱신하는 게 빠르다: `aws ssm start-session --target <InstanceId>`.

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
| 특정 기능만 500이고 화면에는 원인이 안 보임 | 대개 IAM이다. 백엔드 로그의 `AccessDenied`가 어떤 액션·리소스인지 말해 준다(실측 사례: 설문 토큰 인덱스의 `s3:PutObject`, `/admin/users`의 `cognito-idp:*`) |
| 워크스페이스 채팅 내역이 빈 목록 | `list_history`는 모든 실패를 `[]`로 강등하므로 화면만 보면 원인을 알 수 없다. `projects/{pid}/discovery/transcript/`에 객체가 있는지 먼저 확인하고, 없으면 미러링 쪽·있으면 읽기 쪽(세션 키 유도)을 본다 — 위 "Discovery 대화" 항목 참고 |
| 스택이 `ROLLBACK_COMPLETE`라 재배포 거부 | **최초 생성이 실패한 스택은 업데이트가 불가능하다** — 고친 뒤에도 `cdk deploy`가 거부한다. 먼저 내린 다음 다시 배포한다: `npx cdk destroy PathfinderAuthStack` → `npx cdk deploy --all`. `UPDATE_ROLLBACK_COMPLETE`(기존 스택의 업데이트 실패)는 반대로 그냥 재배포하면 된다 |
| 첫 대화 턴에서 `AccessDeniedException` | 배포 리전에 Bedrock 모델 액세스 미활성화. `ANTHROPIC_MODEL`이 IAM 허용 목록 밖이어도 같은 증상 — 아래 "환경 변수 요약"의 허용 값 참고 |
| `` `temperature` is deprecated for this model `` | Opus 4.7 이후 모델은 샘플링 파라미터를 제거했다 — 아래 "참고"의 Bedrock 항목 |
| 로그인 후 `redirect_mismatch` | 호스팅 스택의 콜백 URL 등록(`UpdateUserPoolClient`)이 실패. `cdk deploy PathfinderHostingStack` 재실행 |
| `cdk synth`가 크리덴셜을 요구 | 호스팅 스택의 프리픽스 리스트 lookup. 최초 1회만 필요하며 결과가 `cdk.context.json`에 캐시된다 |
| SSH 접속 불가 | 의도된 설계다. SSH 포트가 없고 SSM만 열려 있다 |

### 삭제

```bash
cd infra && npx cdk destroy --all
```

> ⚠️ 배포 리소스(S3 · IAM · Cognito · EC2/CloudFront)는 **비용이 발생**한다
> (스토리지 + 턴마다 Bedrock 호출 + EC2 상시 가동). 워크숍이 끝나면 내린다.
>
> ⚠️ User Pool은 `RemovalPolicy.DESTROY`이므로 **사용자 계정이 전원 함께 사라진다.**
> S3 아티팩트 버킷에 남기고 싶은 산출물이 있으면 먼저 내려받는다.

스택 내부 설계(콜백 URL 순환 의존, 클라이언트 시크릿 조회, 오리진 보호 등)는
`infra/README.md`에 자세히 있다.

---

## 로컬 개발 실행

프론트(:3000) → 백엔드(:8000) → 백엔드 프로세스 안에서 직접 도는 Discovery 에이전트가
Bedrock을 호출해 응답한다. 백엔드 CORS가 `http://localhost:3000`을 기본 허용하고, 프론트는
기본 `http://localhost:8000`을 호출한다.

에이전트가 Bedrock을 호출하므로 AWS 자격증명(호스트 롤/프로필 — 인스턴스 프로파일이든 로컬
`~/.aws/credentials`든, S3 접근과 동일한 자격증명 체인)과 `PATHFINDER_S3_BUCKET`,
`ANTHROPIC_MODEL`이 필요하다. `backend/.env.example`을 `backend/.env`(gitignored)로 복사해
값을 채우면 기동 시 자동 로드된다(실 환경변수가 파일보다 우선). 값은 위 CDK 배포의
CfnOutputs에서 가져온다 — 로컬 개발만 할 거면 버킷·롤만 있으면 되므로
`npx cdk deploy PathfinderDrillStack`으로 충분하다.

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

브라우저에서 `http://localhost:3000` → 프로젝트 생성 → 대시보드 / 워크스페이스 / 문서 리뷰 /
프로토타입. 워크스페이스에서 메시지를 보내면 실 에이전트가 Bedrock으로 응답한다.

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
| `PATHFINDER_LOG_LEVEL` | `INFO` | 애플리케이션 로그 레벨. `app.configure_logging()`이 기동 시 루트 핸들러를 붙이고 `pathfinder`·`claude_agent_sdk` 로거를 이 레벨로 연다 — 없으면 uvicorn이 자기 로거만 설정하므로 INFO가 조용히 사라진다(실측: journald 2905줄 중 애플리케이션 로그 0건) |
| `PATHFINDER_S3_REGION` | `ap-northeast-2` | 영속 스토리지 리전(서울). **버킷이 만들어진 리전과 반드시 일치**시킬 것 |
| `PATHFINDER_S3_BUCKET` | — | 아티팩트 버킷 (CDK 출력) |
| `ANTHROPIC_MODEL` | — (EC2 배포는 `global.anthropic.claude-opus-4-8`) | Bedrock 추론 프로파일 id. IAM이 invoke를 허용하는 값은 `global.anthropic.claude-{opus-5,opus-4-8,opus-4-7,sonnet-5,sonnet-4-6}`. 기본값은 `infra/lib/backend-permissions.ts`의 `MODEL`이 user-data로 넘긴다 |
| `PATHFINDER_RULES_DIR` | `<repo>/rule/aiplc-rules` | aiplc 룰 디렉토리(읽기 전용) |
| `PATHFINDER_WORKSPACES_DIR` | 시스템 tmp 하위 | 프로젝트별 로컬 워크스페이스 루트 |
| `PATHFINDER_DISCOVERY_DRIVER` | `claude` | Discovery 드라이버. `strands`로 구 드라이버 폴백. 그 외 값은 기동 시 ValueError |
| `PATHFINDER_DISCOVERY_CONFIG_DIR` | `~/pathfinder-discovery-config` | Discovery 에이전트 전용 `CLAUDE_CONFIG_DIR`. proto용과 달라야 한다 — 자세한 내용은 `discovery-config/README.md` |
| `PATHFINDER_PROTO_MAX_CONCURRENT` | `10` | 동시 프로토타입 빌드 상한(전역). 초과 시 세션 시작이 429. 1인 1환경 워크숍 전제로 10 |
| `PATHFINDER_PROTO_CONFIG_DIR` | `~/pathfinder-proto-config` | 빌드 에이전트 전용 `CLAUDE_CONFIG_DIR`. 미지정 시 호스트 유저의 `~/.claude`(개인 skills/agents)가 빌드에 섞인다. CDK 배포 시엔 레포의 `proto-config/`가 그대로 `/opt/pathfinder/proto-config`가 된다 — 빌드 에이전트에 미리 넣어둘 스킬은 `proto-config/skills/<name>/SKILL.md`에 커밋하면 자동 활성화(`skills="all"`). 자세한 내용은 `proto-config/README.md` |
| `PATHFINDER_PROTO_ROOT` | `~/pathfinder-protos` | 프로토타입 빌드 + 호스팅 공용 루트 (EC2 로컬) |
| `PATHFINDER_COGNITO_USER_POOL_ID` | — | Cognito 풀 id. **둘 다 비워야** 인증 전체 바이패스(로컬/테스트 기본). 하나만 비우면 모든 요청이 RuntimeError — 아래 "참고" 참조 |
| `PATHFINDER_COGNITO_CLIENT_ID` | — | 앱 클라이언트 id. access 토큰의 `client_id` 클레임 검증용. **둘 다 비워야** 바이패스, 하나만 비우면 모든 요청이 RuntimeError |
| `PATHFINDER_COGNITO_REGION` | `PATHFINDER_S3_REGION` | 풀이 있는 리전 |

**프론트엔드**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | API base. 원격 프록시 뒤면 `/api` |
| `PATHFINDER_BACKEND_URL` | `http://localhost:8000` | `/api` route handler가 프록시할 백엔드 (server-side) |
| `COGNITO_HOSTED_UI_DOMAIN` | — | Hosted UI 도메인 (server-side only) |
| `COGNITO_CLIENT_ID` | — | 앱 클라이언트 id (server-side only) |
| `COGNITO_CLIENT_SECRET` | — | 토큰 교환용 시크릿. **`NEXT_PUBLIC_` 금지** |
| `APP_BASE_URL` | `http://localhost:3000` | 콜백 URL 조립용 |

---

## 테스트

```bash
# 백엔드 유닛 (AWS 불필요 — fake/Stubber)
cd backend && .venv/bin/python -m pytest -q

# 프론트엔드 유닛 (Vitest + MSW; e2e는 제외)
cd frontend && npm test

# 인프라 합성 + 템플릿 단정 (배포 없이 검증)
cd infra && npm test

# 프론트엔드 e2e (실 백엔드 + 실 Bedrock 자격증명 필요 — INTEGRATION)
cd frontend && npm run test:e2e
```

---

## 참고

- **인증은 Amazon Cognito(Hosted UI v2)** 다. 역할은 `admin`과 `pm` 둘이며 Cognito
  그룹 멤버십이 역할의 유일한 출처다. **self-signup은 차단**되어 있어 신규 계정은
  `/admin/users`에서 관리자가 초대해야 생긴다(초대하면 임시 비밀번호가 화면에 1회
  표시된다 — 이 앱은 메일을 보내지 않는다).

  세션은 **httpOnly 쿠키**에 담기고 same-origin `/api` 프록시가 그것을
  `Authorization: Bearer`로 번역한다. `EventSource`는 커스텀 헤더를 못 보내지만
  쿠키는 자동 전송되므로 SSE도 이 경로로 인증된다.

  **무인증으로 열려 있는 경로는 둘뿐이다**: `/survey/{token}`(익명 설문)과
  `/proto/{pid}/{slug}/*`(프로토타입 프리뷰). 둘 다 계정이 없는 최종 사용자를
  위한 것이며, `backend/tests/test_auth_route_coverage.py`가 이 경계를 강제한다.

  **로컬 개발**은 `PATHFINDER_COGNITO_USER_POOL_ID`와 `PATHFINDER_COGNITO_CLIENT_ID`를
  둘 다 비워두면 인증이 전체 바이패스되어 지금까지와 똑같이 돈다. 둘 다 채우면
  인증이 켜진다. **하나만 채운 상태는 배포 사고로 간주**해 모든 요청에서
  `RuntimeError`를 던진다(fail-closed) — 반쯤 설정된 상태가 조용히 전원을 관리자로
  통과시키는 것보다는 눈에 보이는 실패가 낫다는 판단이다. 로컬에서 인증을 켜고
  검증하려면 `NEXT_PUBLIC_API_BASE_URL=/api`로 띄워야 한다(쿠키는 same-origin에서만
  프록시를 타고 번역된다).
- **Bedrock 모델과 샘플링 파라미터**: `ANTHROPIC_MODEL`은 Discovery 에이전트와 프로토타입
  빌드 에이전트가 공용으로 쓴다. IAM이 invoke를 허용하는 5개(`opus-5`, `opus-4-8`,
  `opus-4-7`, `sonnet-5`, `sonnet-4-6`) 안에서 env 한 줄로 전환할 수 있다
  (`infra/lib/backend-permissions.ts`; 목록을 넓히려면 여기와
  `infra/test/hosting-stack.assert.ts`를 함께 고친다).

  **Claude Opus 4.7 이후 모델(Opus 4.7·4.8·5, Sonnet 5)은 `temperature`/`top_p`/`top_k`와
  `budget_tokens`를 제거했다** — 보내면 요청 전체가 `ValidationException`으로 실패한다
  (`` `temperature` is deprecated for this model ``). 백엔드 드라이버는 원래 보내지 않지만,
  **빌드 에이전트가 생성하는 프로토타입 코드가 이걸 넣으면 런타임에 깨진다.** 그래서
  `proto-config/CLAUDE.md`에 금지 지침을 두어 에이전트가 처음부터 넣지 않게 한다. 모델 ID를
  정규식으로 검사해 특정 모델만 제외하는 우회는 만들지 않는다 — 기본 모델이 env로 바뀌면
  패턴이 새 모델을 놓쳐 같은 에러가 재발한다(실제로 `opus-(4-8|5)` 패턴이 `sonnet-5`를
  놓쳤다). 추론 깊이가 필요하면 `thinking: {type: "adaptive"}`를 쓴다.
- **리전**: 모든 리소스(S3, 백엔드, Discovery 에이전트, 프로토타입 빌드/호스팅)는
  서울(`ap-northeast-2`) 통일이 기본. 다른 리전이 필요하면 `CDK_DEPLOY_REGION`(인프라)과
  `AWS_REGION`/`PATHFINDER_S3_REGION`(백엔드)으로 지정한다 — 세 값이 같은 리전을 가리켜야
  한다. 프로토타입 빌드는 이제 백엔드 프로세스 안에서 직접 돌기 때문에(도쿄 MicroVM 없음)
  더 이상 리전 예외가 없다.
- 진행/결정 기록은 `.superpowers/sdd/progress.md`(git-ignored)와 `docs/superpowers/plans/` 참고.
