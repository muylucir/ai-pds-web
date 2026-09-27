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
없이 열리는 링크로 공유하는 검증 설문까지 이어진다. 화면과 생성물 모두 한국어·영어를 지원하고,
관리자는 사용자·모델 카탈로그·브랜드 디자인 프로필을 같은 앱에서 관리한다.

```
frontend/          Next.js 15 (App Router) — 대시보드 · 워크스페이스 · 문서 리뷰 · 프로토타입 · 설문 · 관리자 · 매뉴얼
backend/           FastAPI — Discovery 에이전트 · SSE 릴레이 · S3 영속화 · 프로토타입 빌드/호스팅 · 설문 · JWT 검증
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

프론트 미들웨어는 UX 게이트일 뿐이고 **보안 경계는 백엔드의 `require_admin`·`require_user`다.**
공개 경로 목록은 양쪽(`frontend/lib/auth/gate.ts`, `backend/tests/test_auth_route_coverage.py`)이
짝으로 단정한다.

| 화면 | 하는 일 | 접근 |
|---|---|---|
| `/` | 프로젝트 목록·생성·내보내기·가져오기 | 로그인 |
| `/projects/{id}/workspace` | Discovery 대화 — 실시간 턴, 질문 시트, 첨부, 문서 패널 | 로그인 |
| `/projects/{id}/dashboard` | 스테이지 진행·산출물·활동 | 로그인 |
| `/projects/{id}/review` | 문서 리뷰와 승인 게이트 | 로그인 |
| `/projects/{id}/questions` | 질문 파일 하나를 펼쳐 답하는 화면 | 로그인 |
| `/projects/{id}/prototypes` | 빌드 세션·호스팅·검증 설문 | 로그인 |
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
| `AipdsHostingStack` | VPC + EC2(AL2023, m7i.2xlarge) + CloudFront |
| `AipdsPreviewStack` | 프로토타입만 서빙하는 별도 CloudFront |
| `AipdsAgentCredsStack` | 샌드박스의 에이전트·프로토타입이 쓰는 Bedrock 전용 롤 |
| `AipdsUploadCorsStack` | 인스턴스가 버킷 CORS에 자기 앱 오리진을 유지하는 권한(프로젝트 가져오기는 S3로 직접 올린다) |

- **15~20분 걸린다.** `cdk deploy`가 끝난 뒤에도 인스턴스가 첫 빌드를 마치는 몇 분 동안
  CloudFront가 502를 줄 수 있다. 그 몇 분 뒤 인스턴스가 샌드박스·IMDS 차단·프리뷰 오리진을
  스스로 켜고(`aipds-harden sync`) 백엔드를 한 번 재시작한다. 손으로 돌릴 것은 없다.
- **배포되는 것은 푸시된 `main`이다.** 인스턴스는 부팅 때 `origin/main`을 clone한다. 푸시하지 않은
  것은 배포되지 않고, 같은 이유로 `cdk deploy`는 코드를 갱신하지 않는다 — 그것은 `sudo aipds-update`다.
- **출력값**: `AipdsHostingStack.DistributionDomain`이 앱 주소, `AipdsPreviewStack.PreviewOrigin`이
  공유 링크가 가리키는 주소, `AipdsHostingStack.InstanceId`가 SSM 대상이다.
- **로그인**은 `admin@aipds.local` 또는 `pm@aipds.local`에 `SeedPassword` 값으로 한다. 30일 동안 유효한
  임시 비밀번호이고 각자 첫 로그인에서 바꾼다. 풀 정책(8자 이상, 대문자·소문자·숫자·기호 각각
  하나 이상)을 만족하지 않으면 CloudFormation이 배포 시작 전에 거부한다.
- **다른 리전**: `CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all …`. 코드 수정은 필요 없다.
- **추가 업로드 오리진**(커스텀 도메인 등): 배포 시 `AIPDS_UPLOAD_ORIGINS`(쉼표 구분). 앱 자신의
  오리진은 인스턴스가 더한다.

코드 갱신, 샌드박스 명령, 떠 있는 인스턴스의 교체 방지, 인스턴스 새로 만들기, 내리기, 문제 해결은
**`/manual` → 설치 · 운영 · 문제 해결**에 있다.

---

## 로컬에서 띄우기

프론트(:3000) → 백엔드(:8000) → 에이전트가 Bedrock을 부른다. 버킷과 롤은 필요하므로
`AipdsDrillStack`을 먼저 배포한다. Python **3.11**, Node.js 20+.

```bash
git submodule update --init --recursive
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && npm install
cp ../backend/.env.example ../backend/.env           # DrillStack 출력값으로 채운다

cd backend && .venv/bin/python -m uvicorn aipds.app:app --host 0.0.0.0 --port 8000 --reload
cd frontend && npm run dev                           # http://localhost:3000
```

로컬에서는 에이전트가 내 사용자로 직접 돈다 — 샌드박스는 인스턴스의 기능이다.

| 변수 | 넣을 값 |
|---|---|
| `AIPDS_S3_BUCKET` / `AIPDS_S3_REGION` | `AipdsDrillStack.ArtifactsBucketName`과 그 리전. 비우면 조용히 로컬 전용으로 돈다 |
| `AIPDS_DISCOVERY_CONFIG_DIR` / `AIPDS_PROTO_CONFIG_DIR` | 리포의 `discovery-config/`·`proto-config/` 절대 경로. 비우면 에이전트가 **내 `~/.claude`**를 읽어 개인 스킬이 결과에 섞인다 |
| `ANTHROPIC_MODEL` | 프로젝트에 모델이 없을 때 쓰는 대체 Bedrock 추론 프로파일 id |
| `AIPDS_COGNITO_USER_POOL_ID` / `AIPDS_COGNITO_CLIENT_ID` | **둘 다** 비우면 인증을 건너뛴다(로컬 기본). 하나만 채우면 모든 요청이 실패한다 |
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

---

## 라이선스

[MIT-0](LICENSE) (MIT No Attribution) — 저작권 고지 보존 의무가 없는 MIT다. 워크숍에서 복사한 이
리포가 그대로 고객의 리포가 될 수 있다.
