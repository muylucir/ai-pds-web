import type { ManualSection } from "../types";

export const operations: ManualSection = {
  id: "operations",
  title: "설치 · 운영 · 문제 해결",
  lede: "AI-PDS Web을 직접 띄우고 관리하는 사람을 위한 절입니다. 쓰는 것만 하려면 여기는 필요 없습니다.",
  blocks: [
    {
      kind: "callout",
      tone: "note",
      md: `이 절은 **AWS 계정에 AI-PDS Web을 배포하는 사람**을 위한 것입니다. 이미 올라간 주소를
받아 쓰는 사용자는 [시작하기](/manual#getting-started)부터 읽으면 됩니다.`,
    },
    { kind: "heading", id: "deploy", text: "배포" },
    {
      kind: "md",
      md: `필요한 것: Node.js 20+, 관리자급 AWS 자격증명(IAM 롤·Cognito·VPC를 만듭니다),
그리고 **배포 리전에서 사용할 Claude 모델의 Bedrock 모델 액세스**.

마지막 항목을 빼먹으면 배포는 성공하고 첫 대화에서 실패합니다 — 가장 흔한 실수입니다.`,
    },
    {
      kind: "cmd",
      caption: "계정·리전 조합당 부트스트랩은 최초 1회만 필요합니다",
      lines: [
        "cd infra",
        "npm ci",
        "npx cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-2",
        "npx cdk deploy --all --require-approval never",
      ],
    },
    {
      kind: "md",
      md: `세 스택이 서로를 참조하므로 **\`--all\`로 함께** 배포합니다.

| 스택 | 만드는 것 |
|---|---|
| \`AipdsDrillStack\` | 산출물 S3 버킷 + 백엔드 실행 롤 |
| \`AipdsAuthStack\` | Cognito User Pool + 로그인 화면 + 역할 그룹 + 시드 계정 |
| \`AipdsHostingStack\` | VPC + EC2 + CloudFront |

**15~20분** 걸립니다. \`cdk deploy\`가 끝난 뒤에도 EC2가 백엔드·프론트를 빌드하고 있을 수 있어
**몇 분간 502가 나오는 것은 정상입니다.**

접속 주소는 출력값 \`AipdsHostingStack.DistributionDomain\` 입니다.`,
    },
    { kind: "heading", id: "migrate", text: "기존 배포에서 옮겨오기" },
    {
      kind: "md",
      md: `스택 이름이 바뀌면 CloudFormation은 기존 스택과의 연결을 잃습니다. 새 스택 3개가
생기고 기존 스택 3개는 남습니다.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**0. 이 변경이 \`main\`에 올라간 뒤에는 기존 인스턴스에서 \`sudo aipds-update\`를 돌리지
마십시오.** 기존 인스턴스의 systemd 유닛과 트리 경로는 옛 이름을 그대로 쓰고 있어 갱신
스크립트가 중간에 실패하고, 그 인스턴스는 아래 9단계로 기존 스택을 지우기 전까지 진행 중인
설문 링크를 계속 서빙해야 하는 바로 그 인스턴스입니다. 기존 스택 3개를 지우기 전까지는 그대로
얼려 두세요.`,
    },
    {
      kind: "steps",
      items: [
        "`cdk deploy AipdsDrillStack AipdsAuthStack` — 새 버킷과 사용자 풀",
        "`aws s3 sync s3://<기존 버킷> s3://<새 버킷>` — 산출물을 옮깁니다. 키 접두사에 제품 이름이 없으므로 구조는 그대로 올라갑니다. 동기화 후 `aws s3api list-objects-v2 --bucket <새 버킷> --query 'length(Contents)'`를 같은 명령의 기존 버킷 결과와 비교해 오브젝트 수가 같은지 확인합니다 — 이 뒤로는 삭제가 되돌릴 수 없으므로, 프로젝트 카드가 보인다는 것만으로는 부분 동기화 실패를 잡지 못합니다",
        "Discovery 대화 기록의 프리픽스를 옮깁니다. 트랜스크립트는 프로젝트 id에서 파생한 UUID 아래 저장되고 그 파생식이 이번 개명으로 바뀌었으므로, 옮기지 않으면 **모든 프로젝트의 Discovery 대화가 빈 세션으로 시작합니다**(산출물·설문·응답은 영향 없습니다). 아래는 이미 있는 디렉터리 이름을 그대로 읽어 새 이름으로 옮기므로, 여러 번 돌려도 안전합니다:\n\n```bash\nfor pid in $(aws s3 ls s3://<새 버킷>/projects/ | awk '{print $2}' | tr -d /); do\n  base=\"s3://<새 버킷>/projects/$pid/discovery/transcript\"\n  cur=$(aws s3 ls \"$base/\" 2>/dev/null | awk '{print $2}' | tr -d / | head -1)\n  want=$(python3 -c \"import uuid,sys;print(uuid.uuid5(uuid.NAMESPACE_URL,'aipds:'+sys.argv[1]))\" \"$pid\")\n  if [ -n \"$cur\" ] && [ \"$cur\" != \"$want\" ]; then\n    aws s3 mv \"$base/$cur/\" \"$base/$want/\" --recursive\n  fi\ndone\n```",
        "`cdk deploy AipdsHostingStack` — 새 EC2와 CloudFront",
        "새 주소로 로그인 확인 (`admin@aipds.local`)",
        "프로젝트 카드 확인 — 명세와 설문은 살아 있고 프로토타입은 **빌드 전**입니다",
        "필요한 프로토타입을 다시 빌드합니다. 빌드 산출물은 인스턴스 디스크에만 있었으므로 함께 오지 않습니다",
        "진행 중이던 설문이 모두 닫힌 뒤 `aws s3 sync s3://<기존 버킷> s3://<새 버킷>`를 다시 실행합니다 — 첫 동기화 이후 기존 주소의 설문 링크로 들어온 응답은 기존 버킷에만 있고, 새 버킷에는 없습니다",
        "기존 스택 3개를 삭제합니다 (Hosting → Auth → Drill 순서)",
      ],
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**9단계를 서둘러 하지 마십시오.** 이미 배포된 설문 링크는 기존 주소를 가리키므로,
기존 스택을 지우면 그 링크가 죽습니다. 진행 중인 설문의 응답 수집이 끝난 뒤 8단계에서 다시
동기화하고, 그 다음에 지웁니다.

기존 Drill 스택은 \`removalPolicy: DESTROY\`와 \`autoDeleteObjects: true\`로 만들어져 있고
버전관리도 켜져 있지 않습니다 — 지우면 그 버킷의 오브젝트가 전부 즉시 사라지고 복구할 방법이
없습니다. 8단계의 재동기화를 건너뛰면, 첫 동기화 뒤 기존 링크로 들어온 응답은 이 삭제와 함께
영구히 사라집니다.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**배포되는 것은 \`main\`의 최신 커밋입니다 — 푸시하지 않은 것은 배포되지 않습니다.** EC2가
부팅할 때 리포를 clone하고 그 시점의 \`origin/main\`으로 맞춥니다. 배포 전에 \`git push\`를 하세요.

여기서 오는 결과가 하나 있습니다: **\`cdk deploy\`는 코드를 갱신하지 않습니다.** 커밋을 밀어도
인스턴스가 교체되지 않으므로, 코드 갱신은 [코드 갱신하기](/manual#redeploy)를 씁니다.`,
    },
    {
      kind: "details",
      summary: "시드 계정과 비밀번호 교체",
      md: `배포하면 관리자 계정과 PM 계정이 하나씩 만들어집니다. 그 비밀번호는 CDK 소스의
상수이므로 **CloudFormation 템플릿과 스택 이벤트에 평문으로 남고, 재배포하면 그 값으로
되돌아갑니다.**

데모·평가용으로는 그대로 써도 되지만, 실제로 운영할 거라면
\`infra/lib/auth-client-config.ts\`의 \`SEED_PASSWORD\`를 교체하고,
시드 계정 대신 [사용자 관리에서 초대한 계정](/manual#invite)을 쓰세요.`,
    },
    { kind: "heading", id: "region", text: "리전 바꾸기" },
    {
      kind: "md",
      md: `기본은 서울(\`ap-northeast-2\`)입니다. 코드를 고칠 필요 없이 환경변수로 바꿉니다.`,
    },
    {
      kind: "cmd",
      lines: ["CDK_DEPLOY_REGION=ap-northeast-1 npx cdk deploy --all --require-approval never"],
    },
    { kind: "heading", id: "redeploy", text: "코드 갱신하기" },
    {
      kind: "md",
      md: `**\`cdk deploy\`가 아닙니다.** 배포에는 커밋이 고정되어 있지 않아 커밋을 밀어도 인스턴스가
교체되지 않고, \`cdk deploy\`는 "no changes"로 끝납니다. 갱신은 인스턴스 안의
\`aipds-update\`가 합니다 — **인스턴스 교체가 없으므로 워크숍 중에도 쓸 수 있습니다.**`,
    },
    {
      kind: "cmd",
      caption: "먼저 푸시하고, SSM으로 들어가 한 줄 실행합니다",
      lines: [
        "git push",
        "aws ssm start-session --target <InstanceId>",
        "sudo aipds-update",
      ],
    },
    {
      kind: "md",
      md: `\`origin/main\`으로 트리를 맞추고 **바뀐 쪽만** 반영합니다.

| 바뀐 것 | 하는 일 | 중단 |
|---|---|---|
| 룰셋(서브모듈)·설정만 | 트리만 갱신 | 없음 (다음 턴부터 새 룰을 읽습니다) |
| 백엔드 | 백엔드 재시작 | 진행 중인 대화·빌드 세션이 끊깁니다 |
| 프론트엔드 | 다시 빌드하고 재시작 | 빌드 1~2분간 접속 중인 사용자에게 오류 |
| 없음 (이미 최신) | 아무것도 하지 않습니다 | 없음 |

- 백엔드 재시작은 **진행 중인 대화와 빌드 세션을 끊습니다.** 대화는 다시 열면 이어지지만,
  도는 빌드 세션은 재개 경로를 탑니다. 프론트·백엔드 갱신은 쉬는 시간에 하세요.
- 무엇이 도는지는 \`git -C /opt/aipds rev-parse HEAD\`로 확인합니다.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**인스턴스에서 파일을 직접 고치지 마세요.** \`aipds-update\`가 트리를 \`main\`에
맞추면서 그 수정을 되돌립니다. 고친 것은 푸시한 뒤 갱신하세요.`,
    },
    { kind: "heading", id: "ruleset", text: "AI-PLC 룰셋은 어디에 있는가" },
    {
      kind: "md",
      md: `대화를 이끄는 방법론(질문·스코어링·산출물 형식)은 이 리포의 코드가 아니라 **상류
AI-PLC 룰셋**에 있습니다. 사본을 두지 않고 \`steering-files/\` **git 서브모듈**로, 상류의 특정
커밋에 고정해 무수정으로 가져옵니다 — 사본을 두면 갈라지고, 정본은 상류이기 때문입니다.
백엔드가 **매 턴** 룰셋을 에이전트 작업 폴더로 복사하므로, 룰이 바뀌면 다음 턴부터 반영됩니다.

여기서 오는 결과가 하나 있습니다: **서브모듈까지 clone해야 합니다.** \`--recurse-submodules\`
없이 clone하면 \`steering-files/\`가 빈 디렉터리로 남고, 그러면 룰셋이 없는 채로 도는데
**에러는 나지 않습니다** — 대화가 방법론을 따르지 않는 것으로만 드러납니다.`,
    },
    {
      kind: "cmd",
      caption: "clone할 때, 또는 이미 clone했다면 뒤늦게 채우기",
      lines: [
        "git clone --recurse-submodules <REPO_URL>",
        "git submodule update --init --recursive",
      ],
    },
    {
      kind: "md",
      md: `상류의 새 룰을 받을 때는 서브모듈 포인터를 옮기고 그 커밋을 푸시한 다음
\`sudo aipds-update\`를 돌립니다. 어느 배포가 어느 룰셋으로 도는지를 기록하는 것이 그 커밋입니다.
**워크플로 자체를 바꿔야 한다면 그 변경이 있을 자리는 이 리포가 아니라 상류입니다.**`,
    },
    {
      kind: "cmd",
      lines: [
        "git submodule update --remote steering-files",
        "git add steering-files && git commit -m \"chore: move the ruleset pointer\" && git push",
      ],
    },
    { kind: "heading", id: "hotfix", text: "인스턴스를 새로 만들기" },
    {
      kind: "md",
      md: `인프라를 바꿨을 때만 필요합니다. \`cdk deploy\`가 인스턴스를 교체하고, 새 인스턴스는
부팅하면서 그 시점의 최신 \`main\`을 가져옵니다. 부팅해 빌드를 마칠 때까지 5~10분이 걸리고
그 사이에는 502가 납니다 — 코드만 바뀐 경우에는 위의 갱신을 쓰세요.`,
    },
    {
      kind: "cmd",
      lines: ["cd infra && npx cdk deploy AipdsHostingStack --require-approval never"],
    },
    { kind: "heading", id: "teardown", text: "내리기" },
    {
      kind: "cmd",
      lines: ["cd infra && npx cdk destroy --all"],
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**User Pool이 함께 삭제되므로 사용자 계정이 전원 사라집니다.** S3에 남기고 싶은
산출물이 있으면 먼저 내려받으세요. 그리고 배포된 상태는 **비용이 계속 발생합니다**
(EC2 상시 가동 + 저장소 + 대화 턴마다 Bedrock 호출) — 쓰지 않을 때는 내리는 편이 낫습니다.`,
    },
    { kind: "heading", id: "troubleshooting", text: "문제 해결" },
    {
      kind: "md",
      md: `| 증상 | 원인과 대처 |
|---|---|
| 배포 직후 CloudFront 502 | EC2 첫 빌드가 진행 중입니다(5~10분). 기다립니다 |
| 첫 대화에서 권한 오류 | 배포 리전에 그 모델의 **Bedrock 모델 액세스**가 꺼져 있습니다 |
| 로그인 후 리다이렉트 오류 | 콜백 URL 등록이 실패한 것입니다. \`cdk deploy AipdsHostingStack\` 재실행 |
| 스택이 \`ROLLBACK_COMPLETE\`라 재배포 거부 | 최초 생성이 실패한 스택은 업데이트할 수 없습니다. 그 스택만 destroy한 뒤 다시 배포합니다 |
| 프로토타입 프리뷰가 404 | 의도된 응답입니다 — [공유 링크](/manual#share)로 들어가야 합니다 |
| 영어 화면인데 문서가 한국어 | 정상입니다 — [문서 언어](/manual#doc-language)는 화면 언어와 별개입니다 |
| 긴 메시지를 보내면 연결이 끊어짐 | 한 번에 보내는 양이 너무 큰 것입니다. 나눠서 보내거나 [파일로 첨부](/manual#attach)하세요 |
| 절전·화면보호기에서 돌아오니 화면이 멈춰 있음 | 잃은 것은 **라이브 뷰뿐**입니다 — AI는 계속 일했고 문서도 저장됐습니다. 자동으로 다시 붙으므로 기다리면 이어서 보이고, 그동안 턴이 끝났으면 새로고침하면 내용이 돌아옵니다 |
| 채팅 기록이 비어 보임 | 인스턴스가 교체된 뒤일 수 있습니다. 새로고침 후에도 비어 있으면 백엔드 로그를 봅니다 |
| 특정 기능만 실패하고 화면에 원인이 없음 | 대개 IAM입니다. 백엔드 로그의 \`AccessDenied\`가 어떤 액션인지 알려 줍니다 |
| SSH로 접속이 안 됨 | 의도된 설계입니다. SSH 포트가 없고 SSM만 열려 있습니다 |

**증상이 화면에 이유를 남기지 않을 때는 백엔드 로그를 먼저 봅니다.**`,
    },
    {
      kind: "cmd",
      caption: "백엔드 로그 — 원인이 여기에만 남는 경우가 많습니다",
      lines: [
        "aws ssm start-session --target <InstanceId>",
        "sudo journalctl -u aipds-backend -f",
      ],
    },
    { kind: "heading", id: "local-dev", text: "로컬에서 띄우기" },
    {
      kind: "md",
      md: `프론트(:3000) → 백엔드(:8000) → 백엔드 안의 에이전트가 Bedrock을 호출합니다.
S3 버킷과 롤은 필요하므로 \`AipdsDrillStack\`만 배포해 두면 됩니다.
Python **3.11**과 Node.js 20+가 필요합니다.`,
    },
    {
      kind: "cmd",
      caption: "최초 1회 설치 후, 터미널 두 개로 실행",
      lines: [
        "git submodule update --init --recursive",
        "cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e \".[dev]\"",
        "cd ../frontend && npm install",
        "cp ../backend/.env.example ../backend/.env",
        "",
        "cd backend && .venv/bin/python -m uvicorn aipds.app:app --port 8000 --reload",
        "cd frontend && npm run dev",
      ],
    },
    {
      kind: "md",
      md: `환경변수 전체 목록은 \`infra/lib/user-data.ts\`의 systemd 유닛에 주석과 함께 있고,
그 밖의 배포 절차는 리포의 \`README.ko.md\`에 있습니다. **설계 판단의 근거는 커밋 메시지와
코드 주석에 있습니다** — "왜 이렇게 되어 있는가"는 \`git log\`로 찾습니다.`,
    },
  ],
};
