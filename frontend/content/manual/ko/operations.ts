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
| \`PathfinderDrillStack\` | 산출물 S3 버킷 + 백엔드 실행 롤 |
| \`PathfinderAuthStack\` | Cognito User Pool + 로그인 화면 + 역할 그룹 + 시드 계정 |
| \`PathfinderHostingStack\` | VPC + EC2 + CloudFront |

**15~20분** 걸립니다. \`cdk deploy\`가 끝난 뒤에도 EC2가 백엔드·프론트를 빌드하고 있을 수 있어
**몇 분간 502가 나오는 것은 정상입니다.**

접속 주소는 출력값 \`PathfinderHostingStack.DistributionDomain\` 입니다.`,
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
\`pathfinder-update\`가 합니다 — **인스턴스 교체가 없으므로 워크숍 중에도 쓸 수 있습니다.**`,
    },
    {
      kind: "cmd",
      caption: "먼저 푸시하고, SSM으로 들어가 한 줄 실행합니다",
      lines: [
        "git push",
        "aws ssm start-session --target <InstanceId>",
        "sudo pathfinder-update",
      ],
    },
    {
      kind: "md",
      md: `\`origin/main\`으로 트리를 맞추고 **바뀐 쪽만** 반영합니다.

| 바뀐 것 | 하는 일 | 중단 |
|---|---|---|
| 룰셋·설정만 | 트리만 갱신 | 없음 (다음 턴부터 새 룰을 읽습니다) |
| 백엔드 | 백엔드 재시작 | 진행 중인 대화·빌드 세션이 끊깁니다 |
| 프론트엔드 | 다시 빌드하고 재시작 | 빌드 1~2분간 접속 중인 사용자에게 오류 |
| 없음 (이미 최신) | 아무것도 하지 않습니다 | 없음 |

- 백엔드 재시작은 **진행 중인 대화와 빌드 세션을 끊습니다.** 대화는 다시 열면 이어지지만,
  도는 빌드 세션은 재개 경로를 탑니다. 프론트·백엔드 갱신은 쉬는 시간에 하세요.
- 무엇이 도는지는 \`git -C /opt/pathfinder rev-parse HEAD\`로 확인합니다.`,
    },
    {
      kind: "callout",
      tone: "warn",
      md: `**인스턴스에서 파일을 직접 고치지 마세요.** \`pathfinder-update\`가 트리를 \`main\`에
맞추면서 그 수정을 되돌립니다. 고친 것은 푸시한 뒤 갱신하세요.`,
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
      lines: ["cd infra && npx cdk deploy PathfinderHostingStack --require-approval never"],
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
| 로그인 후 리다이렉트 오류 | 콜백 URL 등록이 실패한 것입니다. \`cdk deploy PathfinderHostingStack\` 재실행 |
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
        "sudo journalctl -u pathfinder-backend -f",
      ],
    },
    { kind: "heading", id: "local-dev", text: "로컬에서 띄우기" },
    {
      kind: "md",
      md: `프론트(:3000) → 백엔드(:8000) → 백엔드 안의 에이전트가 Bedrock을 호출합니다.
S3 버킷과 롤은 필요하므로 \`PathfinderDrillStack\`만 배포해 두면 됩니다.
Python **3.11**과 Node.js 20+가 필요합니다.`,
    },
    {
      kind: "cmd",
      caption: "최초 1회 설치 후, 터미널 두 개로 실행",
      lines: [
        "cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e \".[dev]\"",
        "cd ../frontend && npm install",
        "cp ../backend/.env.example ../backend/.env",
        "",
        "cd backend && .venv/bin/python -m uvicorn pathfinder.app:app --port 8000 --reload",
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
