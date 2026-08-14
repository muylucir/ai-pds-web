import type { ManualSection } from "../types";

export const operations: ManualSection = {
  id: "operations",
  title: "설치 · 운영 · 문제 해결",
  lede: "Pathfinder를 직접 띄우고 관리하는 사람을 위한 절입니다. 쓰는 것만 하려면 여기는 필요 없습니다.",
  blocks: [
    {
      kind: "callout",
      tone: "note",
      md: `이 절은 **AWS 계정에 Pathfinder를 배포하는 사람**을 위한 것입니다. 이미 올라간 주소를
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
      md: `**배포되는 것은 커밋된 코드가 아니라 현재 워킹 트리입니다.** 미커밋 변경도 그대로
올라가므로 배포 전에 \`git status\`로 확인하세요.`,
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
    { kind: "heading", id: "redeploy", text: "코드만 다시 배포" },
    {
      kind: "cmd",
      lines: ["cd infra && npx cdk deploy PathfinderHostingStack --require-approval never"],
    },
    {
      kind: "md",
      md: `에셋이 바뀌면 EC2가 교체되면서 새 코드로 다시 빌드합니다. 급한 한 줄 수정이라면
SSM으로 인스턴스에 들어가 직접 고치는 편이 빠릅니다.`,
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
그 밖의 배포 절차는 리포의 \`README.md\`에 있습니다. **설계 판단의 근거는 커밋 메시지와
코드 주석에 있습니다** — "왜 이렇게 되어 있는가"는 \`git log\`로 찾습니다.`,
    },
  ],
};
