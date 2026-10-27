# AI-PDS

**한국어** | [English](README.md)

AI-PLC Discovery 워크숍용 대화형 캔버스.

> **IMPORTANT**
> 생성형 AI는 틀릴 수 있습니다. 선택한 AI 모델과 에이전틱 코딩 도구가 만들어낸 결과물과
> 비용은 모두 직접 검토하시기 바랍니다. [AWS Responsible AI
> Policy](https://aws.amazon.com/ai/responsible-ai/policy/)를 참고하세요.

> **Note:** 이 리포지토리의 예제는 **실험·교육 목적**입니다. 개념과 기법을 보여주기 위한
> 것이며, 프로덕션 환경에 그대로 쓰기 위한 것이 아닙니다.

Claude Agent SDK 에이전트가 Discovery 방법론을 구동하고, 프론트엔드가 그 턴을 SSE로 실시간
렌더한다. Discovery가 만든 프로토타입 명세는 같은 화면에서 실물 앱으로 빌드·호스팅되고, 로그인
없이 열리는 링크로 공유하는 검증 설문까지 이어진다. Discovery가 정한 것은 AI-DLC용 인계 패키지 —
vision 문서와 그 뒤의 결정들 — 로 넘어간다. 화면과 생성물 모두 한국어·영어를 지원하고,
관리자는 사용자·모델 카탈로그·브랜드 디자인 프로필을 같은 앱에서 관리한다.

```
frontend/          Next.js 15 (App Router) — 대시보드 · 워크스페이스 · 문서 리뷰 · 프로토타입 · 설문 · 인계 · 관리자 · 매뉴얼
backend/           FastAPI — Discovery 에이전트 · SSE 릴레이 · S3 영속화 · 프로토타입 빌드/호스팅 · 설문 · 인계 패키지 · JWT 검증
infra/             CDK (TypeScript) — 스택 6개, 서울 기본
steering-files/    AI-PLC 룰셋 — aws-samples/sample-ai-plc 서브모듈, 무수정으로 가져온다
discovery-config/  Discovery 에이전트 전용 CLAUDE_CONFIG_DIR
proto-config/      빌드 에이전트 전용 CLAUDE_CONFIG_DIR — 위와 반드시 다른 경로
```

| 찾는 것 | 있는 곳 |
|---|---|
| 화면별 사용법, 관리자 기능, **배포된 환경의 운영과 문제 해결** | 앱의 **`/manual`** (로그인 없이 열린다). 원문: [`frontend/content/manual/`](frontend/content/manual) |
| 스택이 왜 이렇게 생겼는가 | [`infra/README.ko.md`](infra/README.ko.md) |
| 두 config dir을 왜 나눴는가 | [`discovery-config/README.md`](discovery-config/README.md) |
| 그 밖의 "왜 이렇게 되어 있는가" | 그 파일을 건드린 커밋(`git log`)과 코드 주석 |

---

## 화면과 접근 권한

프론트 미들웨어는 UX 게이트일 뿐이고 **보안 경계는 백엔드의 `require_admin`·`require_user`·
`require_project_access`다.** 공개 경로 목록은 양쪽(`frontend/lib/auth/gate.ts`,
`backend/tests/test_auth_route_coverage.py`)이 짝으로 단정한다.

프로젝트에는 소유자 한 명과 열람 멤버가 있다. 소유자와 관리자는 읽고 바꾸고, 멤버는 읽기만 하며, 그 밖의
사용자에게는 없는 프로젝트와 같은 404다(`backend/aipds/project_access.py`). 아래 표의 *프로젝트* 접근이
이것이다.

| 화면 | 하는 일 | 접근 |
|---|---|---|
| `/` | 소유하거나 열람하는 프로젝트(관리자는 전부)의 목록·생성·내보내기·가져오기 | 로그인 |
| `/projects/{id}/workspace` | Discovery 대화 — 실시간 턴, 질문 시트, 첨부, 생성 문서 드로어 | 프로젝트 |
| `/projects/{id}/dashboard` | 스테이지 진행·산출물·활동 | 프로젝트 |
| `/projects/{id}/review` | 문서 리뷰와 승인 게이트 | 프로젝트 |
| `/projects/{id}/questions` | 질문 파일 하나를 펼쳐 답하는 화면 | 프로젝트 |
| `/projects/{id}/prototypes` | 빌드 세션·호스팅·검증 설문 | 프로젝트 |
| `/projects/{id}/handoff` | AI-DLC 인계 — 준비 상태, 보완 질문, AI 제안 확인, 패키지(vision 문서·결정 장부·시작 프롬프트)와 zip | 프로젝트 |
| `/survey/{token}` | 익명 검증 설문 | **공개**(토큰) |
| `/api/proto/{pid}/{slug}` | 빌드된 프로토타입. **프리뷰 도메인(`AipdsPreviewStack`)에서만** 서빙된다. 공유 링크가 접근 쿠키를 심고, 쿠키가 없으면 404가 정답이다 | **공개**(토큰 쿠키) |
| `/admin/users` · `/admin/models` · `/admin/design` | 사용자·역할, 모델 카탈로그, 브랜드 디자인 프로필 | admin |
| `/manual` | 매뉴얼. 계정을 받기 전에 무엇을 하는 도구인지 읽을 수 있도록 로그인 앞에 있다 | **공개** |

---

## AI-PLC 룰셋

[AI-PLC](https://github.com/aws-samples/sample-ai-plc)는 프로덕트 매니저 같은 비개발 역할이 고객
인사이트에서 검증된 프로토타입까지 가도록 돕는 AI 주도 워크플로다 — 페인포인트 분석, 유스케이스
우선순위화, PR/FAQ(Working Backwards), 제품 전략, GTM, 프로토타입 명세. AI-PDS는 채팅 기록만으로는
안 되는 것을 더한다: 브라우저 UI, 실시간 턴, 문서 리뷰, 같은 화면에서 프로토타입과 설문까지
빌드·호스팅하는 것.

룰셋은 **여기에 복사해 두지 않는다.** `steering-files/` 서브모듈로 상류의 특정 커밋에 고정해
무수정으로 쓴다 — 사본은 갈라지고, 정본은 상류다. 서브모듈까지 clone한다:

```bash
git clone --recurse-submodules https://github.com/muylucir/ai-pds-web.git
git submodule update --init --recursive      # 이미 서브모듈 없이 clone했다면
```

`steering-files/`가 비어 있어도 에러는 나지 않는다. 에이전트가 방법론을 따르지 않을 뿐이다.
백엔드가 매 턴 룰셋을 에이전트 워크스페이스로 복사하므로, 서브모듈 포인터를 옮기면
(`git submodule update --remote steering-files` → 커밋·푸시 → `sudo aipds-update`) 다음 턴부터
반영된다. 워크플로 자체의 변경은 상류에서 한다.

---

## 배포

한 번의 `cdk deploy --all`로 로그인할 수 있는 앱이 뜬다. EC2가 이 리포를 clone해 백엔드·프론트를
빌드·기동하고, CloudFront가 그 앞에 붙는다.

**사전 준비**

- Node.js 20+, 관리자급 AWS 자격증명(IAM 롤·Cognito·VPC를 만든다).
- Bedrock에서 Claude를 부를 수 있는 계정. Marketplace 구독은 모델의 첫 호출 때 Bedrock이 만들고
  배포되는 롤은 그 권한을 이미 갖고 있다. 직접 해 둘 것은 **Anthropic 첫 사용 양식**을 계정(또는
  조직의 관리 계정)에서 한 번 제출하는 것과, Marketplace 결제 수단이 있는 것이다. 이것이 없으면
  배포는 성공하고 첫 대화가 `AccessDeniedException`으로 실패한다.

```bash
cd infra
npm ci
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2        # 계정·리전당 최초 1회
npx cdk deploy --all --require-approval never \
  --parameters AipdsAuthStack:SeedPassword='<임시-비밀번호>'
```

| 스택 | 만드는 것 |
|---|---|
| `AipdsDrillStack` | S3 아티팩트 버킷 + 백엔드 실행 롤 |
| `AipdsAuthStack` | Cognito User Pool + 로그인 화면 + `admin`/`pm` 그룹 + 시드 계정 2개 |
| `AipdsHostingStack` | VPC(프라이빗 서브넷 + NAT 1개) + EC2(AL2023, m7i.2xlarge) + VPC 오리진으로 붙는 CloudFront |
| `AipdsPreviewStack` | 프로토타입만 서빙하는 별도 CloudFront |
| `AipdsAgentCredsStack` | 샌드박스의 에이전트·프로토타입이 쓰는 Bedrock 전용 롤 |
| `AipdsUploadCorsStack` | 인스턴스가 버킷 CORS에 자기 앱 오리진을 유지하는 권한(프로젝트 가져오기는 S3로 직접 올린다) |

- **15~20분 걸린다.** `cdk deploy`가 끝난 뒤에도 인스턴스가 첫 빌드를 마치는 몇 분 동안
  CloudFront가 502를 줄 수 있다. 그 몇 분 뒤 인스턴스가 샌드박스·IMDS 차단·프리뷰 오리진을
  스스로 켜고(`aipds-harden sync`) 백엔드를 한 번 재시작한다. 같은 동기화가 프런트엔드에 자기 주소를
  알려 주므로, 로그인은 배포가 끝나고 2분쯤 안에 된다. 손으로 돌릴 것은 없다.
- **배포되는 것은 푸시된 `main`이다.** 인스턴스는 부팅 때 `origin/main`을 clone한다. 푸시하지 않은
  것은 배포되지 않고, 같은 이유로 `cdk deploy`는 코드를 갱신하지 않는다 — 그것은 `sudo aipds-update`다.
- **출력값**: `AipdsHostingStack.DistributionDomain`이 앱 주소, `AipdsPreviewStack.PreviewOrigin`이
  공유 링크가 가리키는 주소, `AipdsHostingStack.InstanceId`가 SSM 대상이다. `VpcOriginId`와
  `OriginPrivateDns`는 인스턴스 쪽 스택만 배포할 때 필요한 값이다(`/manual`).
- **로그인**은 `admin@aipds.local` 또는 `pm@aipds.local`에 `SeedPassword` 값으로 한다. 30일 동안 유효한
  임시 비밀번호이고 각자 첫 로그인에서 바꾼다. 풀 정책(8자 이상, 대문자·소문자·숫자·기호 각각
  하나 이상)을 만족하지 않으면 CloudFormation이 배포 시작 전에 거부한다.
- **MFA는 꺼져 있다.** 로그인은 비밀번호 하나다. 워크숍 샘플로는 충분하지만 실제 고객 자료에는
  부족하다 — 관리자 비밀번호가 새면 사용자·역할 관리까지 넘어간다. 워크숍 밖에서 쓰려면
  `infra/lib/aipds-auth-stack.ts`에서 MFA를 필수로 켠다(`mfa: cognito.Mfa.REQUIRED`와
  `mfaSecondFactor: { otp: true, sms: false }` — 인증 앱).
- **그 밖에도 프로덕션이 아니라 워크숍에 맞춘 설정이 있다.** 각 줄에 바꿀 설정을 적은 경고 주석이 있다.
  - Cognito에 위협 방지(유출된 자격증명 탐지, 위험 기반 인증)가 없다 — Plus 등급이 필요하다
    (`aipds-auth-stack.ts`).
  - `cdk destroy`가 사용자 풀과 아티팩트 버킷을 내용째 지운다. 버킷에는 버전 관리와 액세스 로그도
    없다(`aipds-auth-stack.ts`, `aipds-drill-stack.ts`).
- **다른 리전**: `CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all …`. 코드 수정은 필요 없다. 몇몇
  리전은 AZ 하나에서 VPC 오리진을 제공하지 않는다(us-east-1의 `use1-az3`, us-west-1의 `usw1-az2`,
  ap-northeast-1의 `apne1-az3`, ca-central-1의 `cac1-az3`). AZ 이름이 가리키는 AZ는 계정마다 다르므로
  `aws ec2 describe-availability-zones`로 확인하고, 리전의 앞 두 이름에 그 AZ가 들어 있으면 다른 둘을
  지정한다: `AIPDS_AVAILABILITY_ZONES=ap-northeast-1a,ap-northeast-1c`.
- **추가 업로드 오리진**(커스텀 도메인 등): 배포 시 `AIPDS_UPLOAD_ORIGINS`(쉼표 구분). 앱 자신의
  오리진은 인스턴스가 더한다.

코드 갱신, 샌드박스 명령, 떠 있는 인스턴스의 교체 방지, 인스턴스 새로 만들기, 내리기, 문제 해결은
**`/manual` → 설치 · 운영 · 문제 해결**에 있다.

### CloudFront → 인스턴스 구간은 사설 경로다

인스턴스는 퍼블릭 주소가 없는 프라이빗 서브넷에 있다. 두 배포 — 앱(`aipds-hosting-stack.ts`)과
프리뷰(`aipds-preview-stack.ts`) — 는 CloudFront VPC 오리진 하나로 인스턴스에 닿는다. CloudFront가
VPC 안에 서비스 관리형 네트워크 인터페이스를 두고 그것을 통해 오리진 요청을 보내므로, 이 요청은 공용
인터넷을 지나지 않는다. 인스턴스 자신의 아웃바운드(git, 패키지 설치, Bedrock, 에이전트의 URL 조회)는
NAT 게이트웨이 하나로 나가고, NAT는 시간과 처리한 GB 단위로 과금된다.

**이 구간에 실리는 것:** 세션 쿠키(Cognito 토큰), 프로토타입 접근 쿠키, `X-Origin-Verify` /
`X-Preview-Verify` 비밀 헤더.

**포트 80의 HTTP인 이유.** CloudFront는 오리진 이름에 맞고 공인 인증 기관이 발급한 인증서만 받는데,
EC2로 가는 VPC 오리진의 이름은 인스턴스의 프라이빗 DNS 이름(`ip-….compute.internal`)이고 어느 인증
기관도 이 이름으로 인증서를 발급하지 않는다. 이대로 두어야 자체 도메인 없이 어느 계정에나 배포된다.

**막아 두는 것:**

- 인터넷에서는 인스턴스에 닿을 수 없다: 퍼블릭 IP가 없고, 보안 그룹은 CloudFront 오리진
  대역(프리픽스 리스트)에서 오는 포트 80만 받는다. SSH는 없고 접속은 SSM으로 한다.
- nginx는 비밀 헤더가 없는 요청을 거부한다.
- 비밀 헤더 값은 배포 때 Secrets Manager에서 해석된다. 어느 템플릿에도 평문으로 남지 않는다.

**이 구간에 TLS를 더하려면** 소유한 도메인을 쓴다: 그 이름의 ACM 인증서를 단 내부 Application Load
Balancer를 인스턴스 앞에 두고, 그 로드 밸런서를 `OriginProtocolPolicy.HTTPS_ONLY`의 VPC 오리진으로
삼는다.

---

## 로컬에서 띄우기

프론트(:3000) → 백엔드(:8000) → 에이전트가 Bedrock을 부른다. 버킷과 롤은 필요하므로
`AipdsDrillStack`을 먼저 배포한다. Python **3.11**, Node.js 20+.

```bash
git submodule update --init --recursive
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && npm install
cp ../backend/.env.example ../backend/.env           # DrillStack 출력값으로 채운다

cd backend && .venv/bin/python -m uvicorn aipds.app:app --host 127.0.0.1 --port 8000 --reload
cd frontend && npm run dev                           # http://localhost:3000
```

로컬에서는 에이전트가 내 사용자로 직접 돈다 — 샌드박스는 인스턴스의 기능이다. 인증을 끈 백엔드는 모든 요청을 admin으로 받으므로 `127.0.0.1`에만 연다.

| 변수 | 넣을 값 |
|---|---|
| `AIPDS_S3_BUCKET` / `AIPDS_S3_REGION` | `AipdsDrillStack.ArtifactsBucketName`과 그 리전. 비우면 조용히 로컬 전용으로 돈다 |
| `AIPDS_DISCOVERY_CONFIG_DIR` / `AIPDS_PROTO_CONFIG_DIR` | 리포의 `discovery-config/`·`proto-config/` 절대 경로. 비우면 에이전트가 **내 `~/.claude`**를 읽어 개인 스킬이 결과에 섞인다 |
| `ANTHROPIC_MODEL` | 프로젝트에 모델이 없을 때 쓰는 대체 Bedrock 추론 프로파일 id |
| `AIPDS_COGNITO_USER_POOL_ID` / `AIPDS_COGNITO_CLIENT_ID` | **둘 다** 비우고 `AIPDS_AUTH_DISABLED=1`을 주면 인증을 건너뛴다(`.env.example`의 로컬 기본). 그 스위치 없이 둘 다 비거나 하나만 채우면 백엔드가 뜨지 않는다 |
| `AIPDS_PUBLIC_PATH_PREFIX` | 브라우저가 :8000의 백엔드를 직접 부를 때 `""` |

나머지 변수 전부와, 배포가 쓰는 값과 그 이유는 [`infra/lib/user-data.ts`](infra/lib/user-data.ts)의
systemd 유닛에 주석으로 있다. 기본값은 그 값을 읽는 코드(`backend/aipds/app.py`,
`backend/aipds/cli_settings.py`)에 있다.

**브라우저가 원격이면**(`localhost`가 아니라 프록시 호스트명으로 접속하면) `localhost:8000` 호출이
브라우저 자신의 컴퓨터로 간다. `frontend/.env.local`에 `NEXT_PUBLIC_API_BASE_URL=/api`를 넣어 Next
라우트 핸들러가 백엔드(`AIPDS_BACKEND_URL`, 기본 `http://localhost:8000`)로 프록시하게 하고,
`next.config.mjs`의 `allowedDevOrigins`에 그 호스트명을 더한다.

---

## 테스트

```bash
cd backend && .venv/bin/python -m pytest -q     # 백엔드 유닛 (AWS 불필요)
cd frontend && npm test                         # 프론트 유닛 (Vitest + MSW)
cd infra && npm test                            # 합성 + 템플릿 단정 (배포 없이)
cd frontend && npm run test:e2e                 # e2e (실 백엔드와 Bedrock 필요)
```

| 이것을 돌린다 | 이것을 고쳤을 때 |
|---|---|
| `npm test -- noHardcodedKorean` | 화면 문구 — 딕셔너리를 거치지 않은 한국어 리터럴을 잡는다 |
| `npm test -- parity` | 매뉴얼 — ko/en의 구조·앵커가 어긋난 것을 잡는다 |
| `pytest -q -k sdk_available` | `claude-agent-sdk` 버전 — 옵션 필드와 번들 바이너리를 확인한다 |
| `pytest -q -k no_legacy_brand` | 제품 이름이 들어가는 모든 것 |
| `pytest -q -k no_hardcoded_secrets` | 이름에 token·secret·password가 든 문자열 — 보안심사의 Bandit이 잡는 것(B105/B106/B107)을 미리 잡는다 |
| `pytest -q -k synthetic_email_domains` | 예시 이메일 주소 — 예약 도메인(`example.com`, `.test` 등)만 |

---

## 책임 있는 AI

AI-PDS Web은 Amazon Bedrock의 Claude 모델로 제품 문서, 프로토타입, 설문 종합의 초안을 만든다.
만들어지는 모든 것은 사람이 판단할 초안이고, 도구는 그 전제 위에 만들어져 있다.

**결정은 사람이 한다.** AI는 스스로 단계를 확정하지 않는다. 단계는 사람이 질문지에 답하거나 승인
게이트에서 Discovery 문서를 승인해야 닫히고, 승인할지 수정을 요청할지는 언제나 사람의 몫이다.
*★ AI 추천* 표시는 제안일 뿐, 폼이 대신 골라 제출하는 기본값이 아니다. 인계 패키지도 같은 선을 지킨다:
vision 문서는 모델이 쓰지만, 열린 질문 표는 코드가 모은 후보로 만든다 — 모델이 더하는 행은 근거를
인용해야 하고, 코드가 그 인용이 원문에 있는지 확인한다. 인계 탭에서 아무도 확인하지 않은 AI 제안에는
표시를 단다.

**AI가 한 일이 보인다.** 사용자가 입력한 말과 제출한 답은 AI가 아니라 앱이 `audit.md`에 원문 그대로
실제 시각과 함께 남기고, AI는 그 뒤에 자기 판단을 덧붙인다. 턴마다 실행한 도구와 바뀐 파일은 답변
아래 접힌 진행 기록에 남고, 문서 리뷰 화면은 문서 옆에 AI의 자체 검증 요약을 보여 준다. 답을 제출하면
앱이 그 순간의 문서를 남겨 두므로, 생성 문서 드로어와 문서 리뷰는 답 때문에 바뀐 곳을 표시한다 —
추가·수정·삭제된 블록, 바뀐 표의 행, 그리고 그 변경이 있는 섹션 목록. 프로토타입 명세도 같은 문서로 다룬다.

**근거와 가정을 가른다.** 에이전트 계약은 문서가 중요한 미지수를 밝히고, 가정을 고객 근거로 내세우지
않도록 요구한다. 설문 종합은 응답자의 페르소나별로 결과를 나누므로, 전체 평균이 좋아도 명세가 겨냥한
사용자의 낮은 점수가 가려지지 않고, 대상 사용자 밖의 응답자는 가설을 검증한 것으로 세지 않는다. 그래도
설문은 작고 자발적으로 응한 표본이다 — 종합은 신호로 읽고, 개별 응답 CSV와 대조한다.

**감안해야 할 한계**

- 모델의 출력은 틀리거나, 빠지거나, 지어낸 것일 수 있다. 수치·시장 사실·인용은 워크숍 밖으로 내보내기
  전에 확인한다.
- 프로토타입은 아이디어 검증용으로 쓰고 버리는 코드이지 운영 소프트웨어가 아니며, 계정 없이 열리는
  링크로 공유된다. 실제 개인정보나 기밀 데이터를 프로토타입이나 설문에 넣지 않는다.
- 업로드한 자료는 모델로 전달된다. 워크숍에 필요한 것만 올린다. 자격증명으로 보이는 값은 감사 기록에서
  가려지지만, 그 밖에는 걸러지지 않는다.
- Amazon Bedrock Guardrails는 구성되어 있지 않다. 콘텐츠 필터링이 필요한 용도라면 추가한다 — 예를
  들어 Bedrock을 직접 부르는 프로토타입은 Converse 호출에 가드레일을 함께 넘길 수 있다.
- 배포되는 롤은 Claude를 **global** 교차 리전 추론 프로파일로 호출하고, 이 프로파일은 요청을 지원되는
  모든 상용 AWS 리전에서 처리할 수 있다. 저장되는 데이터는 내 계정과 리전에 남는다. 데이터 상주 요건이
  있다면 지리적 프로파일을 쓰고 `infra/lib/backend-permissions.ts`의 Bedrock 권한을 그에 맞게 좁힌다.

[AWS Responsible AI Policy](https://aws.amazon.com/ai/responsible-ai/policy/)를 참고한다.

---

## 라이선스

[MIT-0](LICENSE) (MIT No Attribution) — 저작권 고지 보존 의무가 없는 MIT다. 워크숍에서 복사한 이
리포가 그대로 고객의 리포가 될 수 있다.
