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
제공한다. 그 프리뷰는 **프로토타입별 접근 토큰**으로 게이트된다 — 공유하는 링크는
`/api/proto/t/{token}`이고, 그것이 프로토타입 경로에 스코프된 쿠키를 심어 준다(아래
"인증" 절). 빌드 트랜스크립트는 S3로 미러링되므로 세션을 닫거나 백엔드가 재시작돼도
맥락이 사라지지 않고, 나중에 다시 시작하면(`resume`) 이전 대화를 이어받는다.

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

워크스페이스 채팅에는 **파일을 첨부**할 수 있다(`POST /projects/{pid}/uploads`,
`AttachmentChips`). `.md`/`.txt`/`.csv`/`.xlsx`/`.pdf`를 받아 **텍스트로 변환해**
워크스페이스에 쓰고 그 경로를 대화에 얹는다 — 원본 바이너리를 에이전트에게 주지
않는다(`parsers/uploads.py`). 상한은 5MB이고, 키에 uuid를 넣어 동시 업로드가 같은
이름을 두고 경합하지 않는다.

자세한 배포 절차는 `infra/README.md` 참고.

프로토타입을 사용자에게 검증할 때는 같은 탭에서 **검증 설문**을 만들 수 있다.
프로토타입 명세의 검증 가설·기능 목록에서 문항을 생성하고(`validation-questionnaire.md`),
인증이 필요 없는 토큰 링크(`/survey/{token}`)를 공유해 익명 응답을 받고, 집계를
대시보드로 확인한 뒤 CSV로 내보내 Discovery의 검증 종합 단계에 넣는다. 응답은 S3에
저장되며(응답 1건 = 객체 1개), 대시보드는 `rollup.json` 캐시를 읽는다.

설문은 **유일한 무인증 쓰기 경로**이므로 상한이 좁게 걸려 있다(`surveys_public.py`):
설문 하나당 응답 1,000건(초과 시 429), 답변 1개당 2,000자, 본문 32KB. 문항에 정의된
키만 저장하고 응답에 내부 식별자(project_id/slug/token)를 되돌려주지 않는다.

문항은 **응답자가 본 것이 데모라는 전제** 위에서 만들어진다(`survey/builder.py`의
`QUESTIONNAIRE_PROMPT_KO`/`_EN` — 프로젝트 언어로 고른다). 성능·보안·실데이터
정확도·도입 시점은 묻지 않고 — 룰이 그
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

같은 이유로 **모델 카탈로그도 버킷 루트(`models/catalog.json`)에 있다** — 프로젝트
생성 화면이 프로젝트가 하나도 없는 상태에서 이것을 읽어야 하므로 프로젝트
프리픽스 안에 둘 수 없다.

**한국어와 영어를 모두 지원한다.** 화면 언어는 상단 네비의 스위치로 언제든 바꾸고,
문서·프로토타입·채팅이 나오는 언어는 프로젝트 생성 시 1회 고른다. 이 둘은 **서로
참조하지 않는 별개 채널**이고, 그렇게 나눈 이유와 각 채널이 지나가는 경로는 아래
"참고"의 언어 항목에 있다.

```
frontend/  Next.js 15 (App Router) — 대시보드 · 워크스페이스 · 문서 리뷰 · 프로토타입 탭 · 로그인/사용자 관리
           (상단 네비는 이 4개다. `projects/[projectId]/canvas`·`/questions`는
            워크스페이스로 대체된 구 화면이 남아 있는 것 — 네비에 노출되지 않는다)
           UI 문구는 전부 `lib/i18n/{ko,en}.ts` 딕셔너리가 소유한다 — 소스에 한국어를
           직접 박으면 `lib/i18n/noHardcodedKorean.test.ts`가 실패한다
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

인프라(`infra/`)는 AWS에 배포할 때 필요하다 — 아래 배포 절 참고. 로컬
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
| `PathfinderDrillStack` | S3 아티팩트 버킷(`projects/*` + `sessions/*` + `surveys/*` + `models/*`) + 백엔드 실행 롤(Bedrock invoke + S3) |
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
> `playwright-report`, `files/*.png`, `.env*`, 빌드 에이전트의 런타임
> 산출물(`proto-type/`·`protos/`·`*-config/projects/`·`*-config/sessions/`),
> 그리고 **이 리포를 개발할 때 쓰는 `.claude/`** 제외 —
> `lib/pathfinder-hosting-stack.ts`). 미커밋 변경도 그대로 배포되므로, 배포 전
> `git status`로 의도한 상태인지 확인한다. `.gitignore`와는 **별개 목록**이므로
> gitignored라고 자동 제외되지는 않는다 — 새로 gitignore한 로컬 산출물은 이
> 목록에도 넣어야 한다. (실제로 `proto-type/`이 빠져 있어서, 개발 박스에서 만든
> 프로토타입이 배포 zip에 실려 새 인스턴스에서 "빌드 완료"로 보였다.)
>
> `.claude/`가 이 목록에 있는 이유는 다른 것들과 다르다. 에이전트의 cwd가
> `/opt/pathfinder/workspaces/{pid}`이고 이 파일은 `/opt/pathfinder/.claude/`에
> 실리므로 **조상**이 된다 — Claude Code는 cwd에서 위로 올라가며 `CLAUDE.md`를
> 전부 로드하므로, 개발용 설정의 한국어 한 줄이 영어 프로젝트의 컨텍스트에 매 턴
> 들어갔다(실측). `CLAUDE_CONFIG_DIR`은 `user` 레벨만 옮기고 조상 탐색은 막지
> 못하므로 **에셋에서 빼는 것이 유일한 차단이다.** `infra/test`가 이 목록을
> 단정한다.

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
| 첫 대화 턴에서 `AccessDeniedException` | **배포 리전에 그 모델의 Bedrock 모델 액세스가 꺼져 있다.** IAM은 `global.anthropic.claude-*`를 전부 허용하므로 이제 IAM이 원인일 가능성은 낮다 — 관리자 화면에서 새 모델을 등록했다면 콘솔에서 그 모델의 액세스를 켰는지 먼저 확인한다(IAM과 별개 설정이다) |
| `` `temperature` is deprecated for this model `` | Opus 4.7 이후 모델은 샘플링 파라미터를 제거했다 — 아래 "참고"의 Bedrock 항목 |
| 영어 프로젝트인데 문서·채팅이 한국어로 나옴 | 언어 지시가 두 레벨에서 충돌한 것이고 **이 실패는 에러를 내지 않는다.** 워크스페이스 `CLAUDE.md`(`place_rules`가 조립한 것) 맨 앞이 `language/en.md`인지, 그리고 상류 룰·공유 config dir·**앱 트리의 조상 `CLAUDE.md`** 에 언어 지시가 되살아나지 않았는지 본다 — 아래 "참고"의 언어 항목 |
| 영어 UI인데 일부 문구만 한국어 | 딕셔너리를 안 타고 소스에 박힌 리터럴이다. `cd frontend && npm test -- noHardcodedKorean`이 위치를 집어 준다 |
| 승인 버튼을 눌러도 게이트가 안 열림 | 턴 텍스트와 판정 정규식이 어긋난 것이다(`lib/approvalMarker.ts`가 둘의 단일 출처). 감사 로그의 `user_input`이 `승인`/`Approved` 중 무엇인지 확인한다 |
| 긴 메시지를 보내면 "연결이 끊어졌습니다" | 요청 라인이 Node `maxHeaderSize`를 넘은 것(HTTP 431). 턴 텍스트가 POST 본문·핸들 경로를 타는지 본다 — 아래 "참고"의 긴 입력 항목 |
| 프로토타입 프리뷰가 404 | **의도된 응답이다** — 접근 토큰 쿠키가 없거나 다른 프로토타입의 것이다. 공유 링크(`/api/proto/t/{token}`)로 들어가야 쿠키가 심긴다. 옛 `/api/proto/{pid}/{slug}/` 링크는 이제 동작하지 않으므로 카드에서 링크를 다시 복사한다. 이 기능 이전에 호스팅 중이던 프로토타입은 토큰이 없으니 **"호스팅 시작"을 다시 눌러** 발급한다. 응답만으로는 원인을 알 수 없으므로(존재를 숨기는 것이 목적이라 없는 프로토타입과 같은 404다) 구별이 필요하면 `PATHFINDER_LOG_LEVEL=DEBUG`로 올린다 — `proto proxy 404: no valid cookie`가 찍힌다 |
| 프리뷰 링크가 어제는 됐는데 오늘 404 | 리셋을 했다면 정상이다(리셋이 폐기 경로다 — `purge()`가 토큰을 지운다). 리셋을 안 했는데 이렇다면 빌드 트리가 사라진 것이다(인스턴스 교체). 토큰은 트리와 수명이 같으므로 다시 빌드·호스팅해야 한다 |
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

브라우저에서 `http://localhost:3000` → 프로젝트 생성(모델과 **생성물 언어**를 여기서
고른다) → 대시보드 / 워크스페이스 / 문서 리뷰 / 프로토타입. 워크스페이스에서 메시지를
보내면 실 에이전트가 Bedrock으로 응답한다. 화면 언어는 상단 네비의 스위치로 언제든
바꿀 수 있다(프로젝트의 생성물 언어와는 별개다 — 아래 "참고"의 언어 항목).

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
| `ANTHROPIC_MODEL` | — (EC2 배포는 `global.anthropic.claude-opus-4-8`) | **폴백** Bedrock 추론 프로파일 id. 프로젝트가 자기 모델을 가지면 그것이 이긴다(`app.project_model`) — 이 값은 이 기능 이전에 만든 프로젝트와 모델 미지정 시에만 쓰인다. IAM은 `global.anthropic.claude-*`를 전부 허용하므로 관리자 화면에서 등록한 모델은 배포 없이 바로 돈다 |
| `PATHFINDER_RULES_DIR` | `<repo>/rule/aiplc-rules` | aiplc 룰 디렉토리(읽기 전용) |
| `PATHFINDER_WORKSPACES_DIR` | 시스템 tmp 하위 | 프로젝트별 로컬 워크스페이스 루트 |
| `PATHFINDER_DISCOVERY_DRIVER` | `claude` | Discovery 드라이버. `strands`로 구 드라이버 폴백. 그 외 값은 기동 시 ValueError |
| `PATHFINDER_DISCOVERY_CONFIG_DIR` | `~/pathfinder-discovery-config` | Discovery 에이전트 전용 `CLAUDE_CONFIG_DIR`. proto용과 달라야 한다 — 자세한 내용은 `discovery-config/README.md` |
| `PATHFINDER_PROTO_MAX_CONCURRENT` | `10` | 동시 프로토타입 빌드 상한(전역). 초과 시 세션 시작이 429. 1인 1환경 워크숍 전제로 10 |
| `PATHFINDER_PROTO_CONFIG_DIR` | `~/pathfinder-proto-config` | 빌드 에이전트 전용 `CLAUDE_CONFIG_DIR`. 미지정 시 호스트 유저의 `~/.claude`(개인 skills/agents)가 빌드에 섞인다. CDK 배포 시엔 레포의 `proto-config/`가 그대로 `/opt/pathfinder/proto-config`가 된다 — 빌드 에이전트에 미리 넣어둘 스킬은 `proto-config/skills/<name>/SKILL.md`에 커밋하면 자동 활성화(`skills="all"`). 자세한 내용은 `proto-config/README.md` |
| `PATHFINDER_PROTO_ROOT` | `~/pathfinder-protos` | 프로토타입 빌드 + 호스팅 공용 루트 (EC2 로컬) |
| `PATHFINDER_PROTO_PERMISSION_MODE` | `bypassPermissions` | 빌드 에이전트의 권한 모드. 빌드는 무인으로 돌아 승인해 줄 사람이 없다 — 더 조이려면 덮어쓴다(잘못된 값은 즉시 ValueError) |
| `PATHFINDER_PUBLIC_PATH_PREFIX` | `/api` | **브라우저가 보는** 프로토타입 프리뷰 마운트. Next가 `basePath`를 빌드 타임에 자산 URL로 굽고 그 URL은 브라우저가 푸므로 이 값이 틀리면 자산이 404가 나고 화면이 스타일 없이 뜬다(자기 교정되는 리다이렉트가 아니다). 백엔드를 :8000으로 직접 부르는 로컬은 마운트가 없으므로 `""` |
| `PATHFINDER_ENV` | — (EC2 배포는 `production`) | `production`이면 프로토타입 접근 쿠키에 `Secure`를 붙인다(`routes/proto_public.py`의 `_cookie_secure` — 프론트가 `NODE_ENV`로 하는 것과 같은 판단이고, 백엔드에는 그런 관습적 변수가 없어 명시한다). 로컬은 비워 둔다: `http://localhost`에서 Secure 쿠키는 저장되지 않아 프리뷰가 열리지 않는다. **기본값이 "Secure 생략"이므로 배포에서 이 값이 빠지면 증상 없이 non-Secure 쿠키가 나간다** — `infra/test/user-data.assert.ts`가 그것을 단정한다 |
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

  **Cognito 인증 없이 열려 있는 경로는 셋뿐이다**: `/survey/{token}`(익명 설문),
  `/proto/t/{token}`(프로토타입 접근 게이트), `/proto/{pid}/{slug}/*`(프리뷰
  프록시). 모두 계정이 없는 최종 사용자를 위한 것이며,
  `backend/tests/test_auth_route_coverage.py`가 이 경계를 강제한다.

  **다만 "계정 없음"이 "누구나"는 아니다.** 프리뷰 프록시는 **프로토타입별 접근
  토큰**으로 게이트된다: `/proto/t/{token}`이 토큰을 `(pid, slug)`로 번역해
  그 프로토타입 경로에만 스코프된 쿠키(`pf_proto_{해시}`, `Path=/api/proto/{pid}/{slug}`,
  HttpOnly, SameSite=Lax, 세션 쿠키)를 심고 프리뷰로 307한다. 쿠키가 없거나 그
  프로토타입의 토큰과 다르면 **404**다(403이 아니다 — 403은 "여기 뭔가 있다"를
  알려주므로, 발견되지 않는 것이 목적인 이 기능과 어긋난다).

  **경로에 토큰을 박지 않고 쿠키를 쓰는 이유**: Next.js `basePath`가 빌드 타임에
  자산 URL·라우터 href·자체 리다이렉트로 구워지므로, 경로 모양을 바꾸면 이미
  빌드된 프로토타입을 전부 재빌드해야 하고(수 분) 토큰이 자산 URL과 Referer로
  새어 나간다. 쿠키는 자산 요청에도 자동으로 붙어 `basePath`를 건드리지 않는다.

  **쿠키가 프로토타입마다 다른 이유**: 공용 쿠키에 `Path=/api/proto`를 주면 한
  링크를 받은 참가자가 다른 프로토타입의 slug를 추측해 들어갈 수 있다 — 막으려는
  구멍이 한 겹 안쪽에서 재현된다.

  토큰은 `{proto_root}/{pid}/{slug}/.proto-token`에 있다(서브된 `prototype/` 트리
  **밖** — 그 안에 두면 핸드오프 zip에 실려 나간다. `_ARCHIVE_EXCLUDED_FILES`가
  이것을 지키고 전용 테스트가 그 값까지 단정한다). 발급은 호스팅 시작 시 한 번이고
  `stop`→`start`를 반복해도 값이 유지된다 — 워크숍 중 호스팅을 껐다 켠 것 때문에
  이미 나눠 준 링크가 죽으면 안 된다. 백엔드 재시작 뒤에는 기동 시
  `load_tokens()`가 디스크에서 다시 읽는다(이것이 없으면 배포된 URL이 전부 404가
  되고, 다시 호스팅해도 URL 안의 토큰은 바뀌지 않으므로 복구되지 않는다). 폐기
  경로는 **리셋**이다 — `purge()`가 트리와 함께 토큰을 지운다.

  프론트 프록시(`app/api/[...path]/route.ts`)는 세션 JWT를 계속 차단하지만
  `pf_proto_*`만 **허용목록**으로 백엔드에 전달한다(`lib/api/proxyAuth.ts`의
  `forwardableCookies`). 허용목록인 것이 load-bearing이다: 차단목록이라면 나중에
  추가되는 세션 쿠키가 조용히 백엔드로 새고 아무 테스트도 깨지지 않는다. `cookie`가
  `route.ts`의 `HOP_BY_HOP`에 **없는 것도 의도된 것**이다 — 지우는 자리가 둘이면
  되살리는 쪽이 반드시 지고, 모든 프리뷰가 404가 된다.

  ⚠️ **남아 있는 한계(의도된 것)**: 링크를 받은 사람의 재공유는 막지 않는다.
  그것까지 막으려면 만료나 개인별 토큰이 필요하고, 둘 다 이 기능의 위협
  모델(URL을 추측으로 찾는 외부인) 밖이다. 프로토타입에 민감 데이터를 넣지
  않는다는 전제는 계속 유효하다.

  **로컬 개발**은 `PATHFINDER_COGNITO_USER_POOL_ID`와 `PATHFINDER_COGNITO_CLIENT_ID`를
  둘 다 비워두면 인증이 전체 바이패스되어 지금까지와 똑같이 돈다. 둘 다 채우면
  인증이 켜진다. **하나만 채운 상태는 배포 사고로 간주**해 모든 요청에서
  `RuntimeError`를 던진다(fail-closed) — 반쯤 설정된 상태가 조용히 전원을 관리자로
  통과시키는 것보다는 눈에 보이는 실패가 낫다는 판단이다. 로컬에서 인증을 켜고
  검증하려면 `NEXT_PUBLIC_API_BASE_URL=/api`로 띄워야 한다(쿠키는 same-origin에서만
  프록시를 타고 번역된다).
- **모델 선택은 프로젝트 단위다.** 프로젝트 생성 화면의 콤보박스에서 고른
  모델이 그 프로젝트의 Discovery 에이전트·프로토타입 빌드 에이전트·설문 문항
  생성에 전부 주입된다(`app.project_model`). 고를 수 있는 목록은 관리자
  화면(`/admin/models`)에서 편집하고 S3의 `models/catalog.json`에 저장된다 —
  파일이 없으면 코드의 시드 4개(Opus 5 / Opus 4.6 / Sonnet 5 / Sonnet 4.6)로
  떨어지고, 그 시드는 관리자가 처음 수정할 때 비로소 파일이 된다.

  **콤보박스에 뜨는 것은 최대 5개**이고, 그 5개를 고르는 것은 관리자가 켜고
  끄는 표시 플래그다(등록 수 자체는 무제한). 여섯 번째를 켜려 하면 400과 함께
  "무엇을 먼저 내리라"는 안내가 온다 — 정렬 상위 5개로 자르면 밀려난 모델이
  화면에서 조용히 사라진다.

  프로젝트가 고른 값은 매니페스트에 **복사**된다. 관리자가 카탈로그에서 그
  모델을 지워도 진행 중인 프로젝트는 계속 같은 모델로 돌고, 헤더 배지에는
  이름 대신 모델 id 원문이 뜬다.

  **IAM은 `global.anthropic.claude-*`를 전부 허용한다**
  (`infra/lib/backend-permissions.ts`). 명시 목록이던 시절에는 관리자가 새
  모델을 등록해도 첫 대화 턴에 `AccessDenied`가 났다 — 화면에서 추가할 수
  있다고 보여주면서 실제로는 `cdk deploy`가 필요한 상태가 최악이라 넓혔다.
  단 **배포 리전에서 그 모델의 Bedrock 액세스가 켜져 있어야** 실제로 돈다
  (IAM과 별개다).

  `PATHFINDER_DISCOVERY_DRIVER=strands` 폴백 드라이버는 프로젝트별 모델을
  **무시하고** 전역 `ANTHROPIC_MODEL`을 쓴다(의도된 범위 제외 —
  `agent/driver.py` 주석).

  **Claude Opus 4.7 이후 모델(Opus 4.7·4.8·5, Sonnet 5)은 `temperature`/`top_p`/`top_k`와
  `budget_tokens`를 제거했다** — 보내면 요청 전체가 `ValidationException`으로 실패한다
  (`` `temperature` is deprecated for this model ``). 백엔드 드라이버는 원래 보내지 않지만,
  **빌드 에이전트가 생성하는 프로토타입 코드가 이걸 넣으면 런타임에 깨진다.** 그래서
  `proto-config/CLAUDE.md`에 금지 지침을 두어 에이전트가 처음부터 넣지 않게 한다. 모델 ID를
  정규식으로 검사해 특정 모델만 제외하는 우회는 만들지 않는다 — 기본 모델이 env로 바뀌면
  패턴이 새 모델을 놓쳐 같은 에러가 재발한다(실제로 `opus-(4-8|5)` 패턴이 `sonnet-5`를
  놓쳤다). 추론 깊이가 필요하면 `thinking: {type: "adaptive"}`를 쓴다.
- **언어는 두 개의 독립된 채널이다.** 서로 참조하지 않는다.

  | | UI 언어 | 생성물 언어 |
  |---|---|---|
  | 범위 | 사용자별 | 프로젝트별 |
  | 저장 | `pf_lang` 쿠키 | `project.json` 매니페스트 |
  | 변경 | 헤더 스위치로 언제든 | **생성 시 1회 결정** |

  **왜 하나로 묶지 않는가.** UI 언어는 언제든 되돌릴 수 있지만 생성물 언어는 그럴
  수 없다 — 이미 만들어진 `aiplc-docs/**`와 CLI 트랜스크립트가 이전 언어로 남기
  때문이다. 워크숍 중간에 바꾸면 한 프로젝트 안에 두 언어가 섞이고 그 상태는
  재현도 설명도 어렵다. 그래서 헤더에 **프로젝트 언어 배지를 읽기 전용으로**
  띄운다 — 영어 UI로 한국어 프로젝트를 열면 문서는 한국어로 나오는 것이 정상이고,
  그것이 화면에 드러나야 한다.

  UI 쪽은 쿠키 기반이고 **경로는 불변이다**(`/ko/...` 세그먼트를 쓰지 않는다).
  로케일 세그먼트를 도입하면 `middleware.ts`의 경로 판정, `safeNext`,
  `rewriteLocation`, 그리고 `/api/proto/{pid}/{slug}/` 프록시 프리픽스가 전부
  그것을 다뤄야 한다 — `trailingSlash`/`basePath` 리다이렉트 루프를 이미 겪은
  프록시 계층을 언어 때문에 다시 건드릴 이유가 없다. `app/layout.tsx`가 쿠키를
  읽는 **유일한 서버 측 지점**이고(`<html lang>` + Provider 초기값), 나머지는 전부
  `useT()`를 쓴다.

  생성물 쪽은 `model_id`가 이미 깐 길을 그대로 쓴다: 매니페스트 → `ProjectRegistry`
  → `place_rules()`·프로토타입 프롬프트·설문 생성/리포트. 미지정은 명시적 `null`로
  기록하고 읽을 때 `ko`로 떨어진다 — 이 기능 이전에 만든 프로젝트는 전부 한국어로
  만들어진 것이므로 그게 사실에 맞다. `get_language()`가 `None`이 아니라 항상
  확정된 값을 반환하는 것은 언어에 "없음"이라는 유효 상태가 없기 때문이다.

  **언어 지시는 한 곳에서만 나온다.** `place_rules`가 워크스페이스 `CLAUDE.md`를
  `language/{ko,en}.md` + `core-workflow.md` 순서로 조립한다. 상류 룰과 공유 config
  dir(`discovery-config/`·`proto-config/`)에서 언어 지시를 **뺐고**, 백엔드
  테스트가 그것이 돌아오지 않았음을 단정한다. 이유는 실제로 겪은 실패다 — 언어
  지시가 두 레벨에 동시에 있으면 어느 쪽이 이길지 예측할 수 없고(문서 양식 바로
  앞의 `**CRITICAL**: Do NOT deviate`가 더 강조돼 있고 맥락도 가까워 이겼다),
  **그 실패는 조용하다.** 문서 절반이 영어로 나와도 에러는 없다. 그래서 언어를
  문서 전체의 **전제로 맨 앞에** 두고, `ko.md`가 그 CRITICAL을 어떻게 읽어야
  하는지까지 설명한다. 영어가 상류 룰의 원래 언어라 `en.md`에는 번역 오버라이드
  절이 아예 없다.

  프롬프트는 **조립하지 않고 언어별로 두 벌을 완성된 문장으로** 유지한다
  (`agent/prompts.py`, `proto/prompts.py`, `survey/builder.py`). 빌드 에이전트는
  `bypassPermissions`로 돌아 Write/Edit이 자동 승인되므로 "계획만 세우고 빌드하지
  마"를 이 텍스트 밖에서 강제할 방법이 없다 — 치환으로 문장을 쪼개면 그 지시의
  강도가 어느 언어에서 약해졌는지 알 수 없게 된다. **도구 설명과 거부 메시지도
  모델이 읽는 프롬프트이므로** 같이 두 벌이다.

  언어 결합이 **로직을 깨뜨리는** 지점은 따로 처리했다:
  - **승인 게이트** — 턴 텍스트와 판정 정규식이 `lib/approvalMarker.ts` 한 모듈에서
    나온다. 한쪽만 바뀌면 게이트가 조용히 열리지 않기 때문이다. 보내는 단어는 **UI
    언어가 아니라 프로젝트 언어**를 따르고(대화가 그 언어로 진행 중이므로), 판정은
    `/^\s*(승인|Approved)\s*$/i`로 두 언어를 다 받는다 — 기존 한국어 감사 로그가
    계속 인식돼야 한다. 불투명 마커를 쓰지 않는 이유는 이 텍스트가 기계 신호가
    아니라 에이전트에게 가고 사람이 읽는 발화이기 때문이다.
  - **중단 마커** — 반대로 이건 순수한 기계 신호다(에이전트가 읽지 않고
    트랜스크립트에도 안 남는다). 백엔드가 `text="interrupted"`를 보내고 화면 문구는
    프론트가 UI 언어로 그린다.
  - **백엔드 HTTP 에러** — `detail`이 안정적인 코드 문자열이고
    (`error_codes.py`) 문구는 프론트 딕셔너리가 소유한다. 백엔드는 UI 언어를
    모르고(프록시가 `Accept-Language`를 넘기지 않으며, 넘겨도 브라우저 값이 쿠키
    스위치와 어긋난다), 백엔드에 두 번째 번역 시스템을 만들지 않는다. 프론트가
    모르는 코드는 원문을 그대로 보여준다. 예외는 `survey/report_labels.py`인데
    그쪽은 UI 문구가 아니라 **문서 생성기**이고 프로젝트 언어는 백엔드가 이미 안다.

  **자동 테스트로는 여기까지다** — `CLAUDE.md`가 어떻게 조립됐는지는 확인할 수
  있지만 모델이 그것을 따랐는지는 확인할 수 없다. 컴포넌트 테스트도 기본
  로케일(ko)로 렌더하므로 번역된 문구와 하드코딩된 문구를 구별하지 못한다(그래서
  `noHardcodedKorean.test.ts`가 기계로 판정한다). 워크숍 전에 한국어/영어 프로젝트를
  각각 Envision까지 돌려 **채팅 말풍선, PR/FAQ의 `Q:` 문구, product-strategy·
  go-to-market의 표 라벨, 섹션 헤딩**을 눈으로 확인한다 — 이 네 곳이 과거에 실제로
  어긋났던 지점이다.

  범위 밖: 이미 생성된 문서의 사후 번역, 생성 후 언어 변경, 생성된 프로토타입 앱의
  i18n(단일 언어 데모다), 제3언어. `strands` 폴백 드라이버는 언어를 처리하지 않는다.
- **긴 채팅 입력은 URL이 아니라 POST 본문으로 간다.** `EventSource`는 GET만
  지원하므로 턴 텍스트가 원래 SSE 쿼리스트링에 실렸는데, 한글은
  `encodeURIComponent`로 한 글자가 9바이트가 된다 — 2,164자 입력이 14,376바이트
  요청 라인이 되고, 여기에 Cognito JWT 쿠키(약 3.7KB)가 더해져 Node의
  `maxHeaderSize` 기본값 16,384바이트를 넘어 프록시가 **431**로 거절했다. 화면에는
  "연결이 끊어졌습니다"만 떴다 — `EventSource`는 HTTP 상태를 노출하지 않아 431이든
  네트워크 단절이든 `onerror`만 발화한다. 이제 텍스트를 POST로 받아 짧은
  핸들(인메모리·1회용·60초)로 바꾸고 URL에는 그것만 싣는다(`turn_handles.py`,
  세 스트림 전부). `EventSource`를 유지한 이유는 이 프록시 계층이 HTTP/2에서 SSE가
  깨지는 문제를 이미 겪은 곳이라, 재연결과 쿠키 인증이 브라우저에 내장된 것을 두고
  원인인 URL 길이만 없앴다.
- **리전**: 모든 리소스(S3, 백엔드, Discovery 에이전트, 프로토타입 빌드/호스팅)는
  서울(`ap-northeast-2`) 통일이 기본. 다른 리전이 필요하면 `CDK_DEPLOY_REGION`(인프라)과
  `AWS_REGION`/`PATHFINDER_S3_REGION`(백엔드)으로 지정한다 — 세 값이 같은 리전을 가리켜야
  한다. 프로토타입 빌드는 이제 백엔드 프로세스 안에서 직접 돌기 때문에(도쿄 MicroVM 없음)
  더 이상 리전 예외가 없다.
- 설계 판단의 **근거는 커밋 메시지와 코드 주석에 있다.** 스펙·계획서는 리포에서
  제거했으므로(사내 산출문서), "왜 이렇게 되어 있는가"는 `git log`로 찾는다 —
  이 README의 각 항목이 그 판단을 요약하고 있고, 더 자세한 것은 해당 파일을
  건드린 커밋의 본문에 있다.
