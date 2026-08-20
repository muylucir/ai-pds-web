# Pathfinder

**한국어** | [English](README.md)

AI-PLC Discovery 워크숍용 대화형 캔버스.

> **IMPORTANT**
> 생성형 AI는 틀릴 수 있습니다. 선택한 AI 모델과 에이전틱 코딩 도구가 만들어낸 결과물과
> 비용은 모두 직접 검토하시기 바랍니다. [AWS Responsible AI
> Policy](https://aws.amazon.com/ai/responsible-ai/policy/)를 참고하세요.

> **Note:** 이 리포지토리의 예제는 **실험·교육 목적**입니다. 개념과 기법을 보여주기 위한
> 것이며, 프로덕션 환경에 그대로 쓰기 위한 것이 아닙니다.

Claude Agent SDK 에이전트가 백엔드 프로세스 안에서 Discovery 방법론을 구동하고,
프론트엔드가 그 턴을 SSE로 실시간 렌더한다. Discovery가 만든 프로토타입 스펙은 같은
화면에서 실물 앱으로 빌드·호스팅되고, 무인증 토큰 링크로 공유하는 검증 설문까지
이어진다. 화면과 생성물 모두 한국어·영어를 지원한다.

```
frontend/  Next.js 15 (App Router) — 대시보드 · 워크스페이스 · 문서 리뷰 · 프로토타입
backend/   FastAPI — Discovery 에이전트 · SSE 릴레이 · S3 영속화 · 프로토타입 빌드/호스팅 · JWT 검증
infra/     CDK (TypeScript) — S3 + 백엔드 롤 + Cognito + EC2/CloudFront (서울 기본)
```

- 스택 내부 구조(콜백 URL 순환 의존, 오리진 보호 등): [`infra/README.ko.md`](infra/README.ko.md)
- 사용 방법(화면별 조작·관리자·운영): 앱의 **`/manual`** — 로그인 없이 열린다
- **설계 판단의 근거는 커밋 메시지와 코드 주석에 있다.** "왜 이렇게 되어 있는가"는
  `git log`로 찾는다 — 해당 파일을 건드린 커밋 본문에 근거가 있다.

---

## AI-PLC — AI-Driven Product Life Cycle with Product Discovery, Strategy and Prototyping

AI-PLC는 프로덕트 매니저·비즈니스 리더 등 **비개발 역할**이 제품 전략을 정의하고 무엇을
만들어야 하는지 판단하도록 돕는 AI 주도 워크플로다. 워크플로를 쓰는 방식은 에이전틱 AI 도구와
자연어로 대화하는 것이고, 고객 인사이트에서 검증된 프로토타입까지를 **한 세션 안에서** 지난다.
페인포인트 분석, 유스케이스 우선순위화, PR/FAQ 작성(Working Backwards), 제품 전략, GTM 전략,
프로토타입 생성을 다룬다.

워크플로는 유연하다 — **지금 있는 지점에서 시작할 수 있다.** 고객 페인포인트를 처음
탐색하는 중이든, 평가·우선순위화할 유스케이스 목록을 이미 갖고 있든, 기존 스펙에서 곧바로
프로토타입 빌드로 뛰어들든 상관없다. 전체 여정을 한 세션에서 끝낼 수도 있고, 이식 가능한
`PROTOTYPE-*.md`를 만들어 다른 팀이 자기 워크스페이스에서 프로토타입을 만들도록 넘길 수도 있다.

워크플로 자체도 **필요에 맞게 고칠 수 있다** — 마크다운 파일로 정의되어 있어 질문, 스코어링
프레임워크, 산출물 형식을 조정하거나 조직에 맞는 도메인 지침을 넣을 수 있다.

**이 리포에서 그 워크플로가 있는 자리.** Pathfinder가 구동하는 룰셋은
[aws-samples/sample-ai-plc](https://github.com/aws-samples/sample-ai-plc)의 AI-PLC 워크플로이며,
여기에는 [`rule/aiplc-rules/`](rule/aiplc-rules)로 들어 있다 — 고칠 대상은 그 마크다운 파일들이다.
백엔드가 **매 턴** 그것을 에이전트 워크스페이스로 복사하므로(`backend/pathfinder/agent/workspace_rules.py`),
룰셋을 고치면 다음 턴부터 반영된다: 재시작도, 재배포도 필요 없다. Pathfinder가 그 워크플로
바깥에 더하는 것은 채팅 기록만으로는 안 되는 부분이다 — 비개발 역할이 쓰는 브라우저 UI, 턴의
실시간 렌더, 문서 리뷰, 그리고 같은 화면에서 프로토타입과 검증 설문까지 빌드·호스팅하는 것.

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

> ⚠️ **배포되는 것은 `main`의 최신 커밋이다 — 푸시하지 않은 것은 배포되지 않는다.** EC2가
> 부팅할 때 공개 리포를 clone하고 그 시점의 `origin/main`으로 맞춘다. 커밋 SHA를 고정하지
> 않으므로 배포 전에 "이 커밋을 푸시했는가"를 따질 일이 없다 — 푸시된 것만 배포된다.
>
> **대가: `cdk deploy`는 코드를 갱신하지 않는다.** user-data에 SHA가 없어 커밋을 밀어도
> user-data가 그대로이고, 그러면 CloudFormation이 인스턴스를 교체하지 않는다. 코드 갱신은
> [`pathfinder-update`](#코드-갱신하기)가 한다.
>
> 인스턴스에서 무엇이 도는지는 `git -C /opt/pathfinder rev-parse HEAD`로 확인한다(부팅
> 시점의 커밋은 부트스트랩 로그의 `booted commit:` 줄에도 남는다).
>
> 종전에는 리포 루트를 zip 에셋으로 올렸다. clone으로 바꾼 이유는 에셋이 **gitignore된
> 파일까지 실었기** 때문이다 — 그래서 별도의 제외 목록을 사람이 관리해야 했고, 그 목록에서
> 빠진 것이 두 번 사고를 냈다(개발 박스의 `proto-type/`이 실려 아무도 빌드하지 않은
> 프로토타입이 "빌드 완료"로 보인 것, 개발용 `.claude/CLAUDE.md`가 에이전트 cwd의 **조상**이
> 되어 한국어 한 줄이 영어 프로젝트 컨텍스트에 매 턴 들어간 것). clone은 tracked 파일만
> 가져오므로 그 실패 종류가 사라졌고, 남은 불변식은 `infra/test/deployed-tree.assert.ts`가
> `git ls-files`로 단정한다.

### 4. 출력값

```
PathfinderHostingStack.DistributionDomain → 접속 URL (https://dxxxx.cloudfront.net)
PathfinderHostingStack.InstanceId         → aws ssm start-session --target <id>
PathfinderDrillStack.ArtifactsBucketName  → AIPDS_S3_BUCKET
PathfinderDrillStack.BackendRoleArn       → 백엔드가 이 롤(또는 동등 정책)로 실행돼야 함
PathfinderDrillStack.Region               → AWS_REGION / AIPDS_S3_REGION
PathfinderAuthStack.UserPoolId            → AIPDS_COGNITO_USER_POOL_ID
PathfinderAuthStack.UserPoolClientId      → AIPDS_COGNITO_CLIENT_ID / COGNITO_CLIENT_ID
PathfinderAuthStack.HostedUiDomain        → COGNITO_HOSTED_UI_DOMAIN
```

EC2 배포에서는 user-data가 이 값들을 자동으로 백엔드/프론트 env에 넣는다 — 손으로 설정할
필요가 없다. 위 매핑은 **로컬 개발에서 같은 인프라를 쓸 때** 참고한다.

### 5. 접속

`DistributionDomain`으로 접속해 시드 계정으로 로그인한다:

| 계정 | 역할 | 비밀번호 |
|---|---|---|
| `admin@aipds.local` | 관리자 (사용자 관리 가능) | `AiPdsWeb2026@!` |
| `pm@aipds.local` | PM | `AiPdsWeb2026@!` |

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

### 코드 갱신하기

**`cdk deploy`가 아니다.** 배포에는 커밋 SHA가 없으므로 커밋을 밀어도 user-data가 바뀌지
않고, 그러면 CloudFormation이 인스턴스를 교체하지 않는다 — `cdk deploy`는 "no changes"로
끝난다. 코드 갱신은 인스턴스 위의 `pathfinder-update`가 한다:

```bash
git push                                       # 배포되는 것은 푸시된 main이다
aws ssm start-session --target <InstanceId>
sudo pathfinder-update
```

`origin/main`으로 트리를 맞추고, **바뀐 쪽만** 반영한다:

| 바뀐 것 | 하는 일 | 중단 |
|---|---|---|
| `rule/`·config dir만 | 트리만 갱신 | 없음 (다음 턴부터 새 룰을 읽는다) |
| `backend/` | (`pyproject.toml`이 바뀐 경우만 재설치 후) 백엔드 재시작 | 진행 중인 턴·빌드 세션이 끊긴다 |
| `frontend/` | (`package-lock.json`이 바뀐 경우만 `npm ci` 후) `next build` + 재시작 | 빌드 1~2분간 청크 404 |
| 없음 (이미 최신) | 아무것도 하지 않는다 | 없음 |

인스턴스 교체(5~10분 502)가 없으므로 워크숍 중에도 쓸 수 있다. 다만 위 표의 "중단"은
남아 있으니, 프론트·백엔드 변경은 쉬는 시간에 반영한다.

- 백엔드 재시작은 **진행 중인 Discovery 턴과 빌드 세션을 끊는다.** 트랜스크립트는 S3에
  미러링되므로 대화는 이어지지만, 도는 빌드 세션은 완료 선언 없이 죽어 재개 경로를 탄다.
- 인스턴스에서 손으로 고친 tracked 파일은 **되돌아간다**(`checkout -f`). 그런 파일 하나가
  갱신 전체를 막는 것이 더 나쁘다는 판단이다 — 인스턴스에서 직접 편집하지 말고 푸시한다.
  `protos/`·`workspaces/`·세션 상태는 untracked라 지워지지 않는다.
- 확인: `git -C /opt/pathfinder rev-parse HEAD` 로 무엇이 도는지 보고,
  `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/`로 앱을 직접 찍는다
  (nginx는 CloudFront의 비밀 헤더가 없으면 403이므로 우회해서 본다).

### 인스턴스를 새로 만들기

인프라를 바꿨을 때(user-data·인스턴스 타입·nginx 설정 등)는 `cdk deploy`가 인스턴스를
교체하고, 새 인스턴스는 부팅하면서 그 시점의 최신 `main`을 가져온다:

```bash
cd infra && npx cdk deploy PathfinderHostingStack --require-approval never
```

부팅해 빌드를 마칠 때까지 5~10분이 걸리고 그 사이 502가 난다. 코드만 바뀐 경우에는 이
경로가 필요 없다 — 위의 `pathfinder-update`를 쓴다.

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
| 프로토타입 프리뷰가 404 | **의도된 응답이다** — 접근 토큰 쿠키가 없거나 다른 프로토타입의 것이다. 공유 링크(`/api/proto/t/{token}`)로 들어가야 쿠키가 심긴다. 분기 조건은 `backend/pathfinder/routes/proto_public.py` |
| 영어 프로젝트인데 문서·채팅이 한국어로 나옴 | 언어 지시가 두 레벨에서 충돌한 것이고 **이 실패는 에러를 내지 않는다.** 프로젝트 언어는 `backend/pathfinder/agent/language/{ko,en}.md`와 공유 config dir(`proto-config/CLAUDE.md`·`discovery-config/CLAUDE.md`) 두 채널로 들어간다 — 둘이 어긋나면 화면은 정상인데 산출물만 다른 언어가 된다 |
| 영어 UI인데 일부 문구만 한국어 | 딕셔너리를 안 타고 소스에 박힌 리터럴이다. `cd frontend && npm test -- noHardcodedKorean`이 위치를 집어 준다 |
| 워크스페이스 채팅 내역이 빈 목록 | `list_history`가 모든 실패를 `[]`로 강등한다. `projects/{pid}/discovery/transcript/`에 객체가 있는지부터 확인한다 — 미러링 키는 project_id에서 uuid5로 유도하므로(`agent/session_store.py`, `agent/claude_driver.py`) project_id를 그대로 프리픽스에 넣어 찾으면 빈 곳을 뒤진다 |
| 긴 메시지를 보내면 "연결이 끊어졌습니다" | 요청 라인이 Node `maxHeaderSize`를 넘은 것(HTTP 431)이고 `EventSource`가 상태 코드를 노출하지 않아 이 문구만 뜬다. 지금은 턴 텍스트를 POST로 받아 1회용 핸들만 URL에 싣는다(`turn_handles.py`) — 다시 나면 입력을 나눠 보내거나 파일로 첨부한다 |

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
cd backend && .venv/bin/python -m uvicorn aipds.app:app --host 0.0.0.0 --port 8000 --reload

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
# (백엔드가 다른 호스트/포트면) AIPDS_BACKEND_URL=http://localhost:8000
```

dev cross-origin 경고를 없애려면 `next.config.mjs`의 `allowedDevOrigins`에 그 호스트명을
넣는다. **이 `/api` 프록시는 dev/데모 편의이며, 프로덕션은 실 리버스 프록시로 대체한다.**

---

## 환경 변수

EC2 배포는 user-data가 전부 채운다. 아래는 **로컬에서 손으로 설정하는 것들**이다.

전체 목록은 두 곳에서 본다: 배포에 실제로 들어가는 값과 그 이유는
[`infra/lib/user-data.ts`](infra/lib/user-data.ts)의 systemd 유닛(`Environment=` 줄마다
주석이 붙어 있다), 기본값과 허용 범위는 이를 읽는 코드
(`backend/pathfinder/app.py`·`backend/pathfinder/cli_settings.py`)에 있다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AIPDS_S3_BUCKET` | — | 아티팩트 버킷(CDK 출력) |
| `AIPDS_S3_REGION` | `ap-northeast-2` | 영속 스토리지 리전. **버킷이 만들어진 리전과 일치**시킬 것 |
| `ANTHROPIC_MODEL` | — (EC2는 `global.anthropic.claude-opus-4-8`) | **폴백** Bedrock 추론 프로파일 id. 프로젝트가 자기 모델을 가지면 그것이 이긴다 |
| `AIPDS_CORS_ORIGINS` | `http://localhost:3000` | 콤마 구분 허용 origin |
| `AIPDS_LOG_LEVEL` | `INFO` | 애플리케이션 로그 레벨(`app.configure_logging()`) |
| `AIPDS_COGNITO_USER_POOL_ID` / `_CLIENT_ID` | — | **둘 다 비우면** 인증 전체 바이패스(로컬 기본). 하나만 비우면 모든 요청이 RuntimeError(fail-closed) |
| `AIPDS_COOKIE_SECURE` | `false` (EC2는 `true`) | 프로토타입 접근 쿠키에 `Secure`를 붙일지. 로컬은 끈 채로 둔다 |
| `AIPDS_AUTO_COMPACT_WINDOW` | — (CLI 기본값) | 자동 컴팩션이 발동하는 컨텍스트 크기(토큰, 100000~1000000). 늦추면 후반 스테이지가 요약이 아닌 근거로 문서를 쓴다 — 대가는 턴당 비용 |
| `AIPDS_LONG_CONTEXT` | `false` | 모델 id에 CLI의 `[1m]`(1M 컨텍스트 베타)을 붙일지. **상위호환이 아니다** — 비용·품질 대가는 `backend/pathfinder/cli_settings.py` 참고 |
| `AIPDS_FILE_QUESTIONS` | `true` | 에이전트가 **질문 파일을 써서** 묻게 할지(Pathfinder가 그 파일을 읽어 적은 그대로 보여준다). falsy로 두면 AskUserQuestion 도구 경로로 돌아간다 — 탈출로로 남겨 둔다. 기본값의 실측 근거: 파일에 쓴 질문을 도구로 다시 만들면서 19문항 중 15개가 훼손됐다(한글 문자 치환, 축약으로 답변 유실). `backend/pathfinder/agent/claude_driver.py`의 `FILE_QUESTIONS_ENV` 참고 |
| `AIPDS_PUBLIC_PATH_PREFIX` | `/api` | **브라우저가 보는** 프리뷰 마운트. 백엔드를 :8000으로 직접 부르는 로컬은 `""` |
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

---

## 라이선스

[MIT-0](LICENSE) (MIT No Attribution). MIT와 같되 **저작권 고지 보존 의무가 없다** —
가져다 쓰는 쪽이 LICENSE 파일을 들고 다니지 않아도 되고, 워크숍에서 이 리포를 복사해
고객 리포로 만드는 사용 방식에 고지 의무를 얹지 않는다.

SPDX 식별자는 각 패키지 메타데이터에도 있다: `backend/pyproject.toml`,
`frontend/package.json`, `infra/package.json`.
