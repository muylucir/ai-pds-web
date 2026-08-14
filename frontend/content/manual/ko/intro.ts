import type { ManualSection } from "../types";

export const intro: ManualSection = {
  id: "intro",
  title: "Pathfinder란",
  lede: "AI-PLC Discovery를 웹 브라우저에서 대화로 진행하는 도구입니다. 개발 도구를 설치하지 않아도 됩니다.",
  blocks: [
    {
      kind: "md",
      md: `Pathfinder는 **제품 기획자가 아이디어를 검증 가능한 형태까지 밀어내는 과정**을 돕습니다.
채팅으로 질문에 답하면 AI가 문서를 쓰고, 그 문서에서 프로토타입을 실제로 만들어 실행하고,
그 프로토타입에 대한 반응을 설문으로 모아 다시 문서에 반영합니다.

터미널·git·에디터를 쓰지 않습니다. 로그인해서 브라우저에서 하는 일이 전부입니다.`,
    },
    { kind: "heading", id: "what-you-get", text: "무엇이 만들어지는가" },
    {
      kind: "md",
      md: `| 산출물 | 어디서 보는가 |
|---|---|
| Discovery 문서 (마크다운) | 문서 리뷰 탭 — 개별 \`.md\` 또는 전체 \`.zip\`으로 내려받습니다 |
| 프로토타입 명세 \`PROTOTYPE-*.md\` | 문서 리뷰 탭. 이 파일만 뽑아 개발팀에 넘길 수도 있습니다 |
| 실행되는 프로토타입 | 프로토타입 탭 — 링크로 공유하면 계정 없이 열립니다 |
| 검증 설문 결과 | 프로토타입 탭의 설문 패널 — 집계 화면과 CSV |
| 감사 기록 \`audit.md\` | 문서 리뷰 탭 — 입력한 말이 원문 그대로 남습니다 |`,
    },
    { kind: "heading", id: "flow", text: "전체 흐름" },
    {
      kind: "diagram",
      id: "entry-points",
      caption: "시작하는 방법은 세 가지이고, 모두 프로토타입에서 만납니다.",
      nodes: {
        pain: { label: "고객 문제에서 시작", to: "start" },
        usecase: { label: "유스케이스에서 시작", to: "start" },
        spec: { label: "이미 있는 명세에서 시작", to: "prototypes" },
        build: { label: "프로토타입 만들기", to: "prototypes" },
        validate: { label: "설문으로 검증", to: "survey" },
        ship: { label: "제품 전략 · 시장 진입" },
      },
    },
    {
      kind: "md",
      md: `- **고객 문제에서 시작** — 페인 포인트를 모아 PR/FAQ를 쓰고 거기서 솔루션을 도출합니다.
- **유스케이스에서 시작** — 이미 후보 목록이 있다면 우선순위화부터 합니다.
- **이미 있는 프로토타입 명세에서 시작** — \`PROTOTYPE-*.md\`가 있으면 앞 단계를 건너뛰고 바로 만듭니다.

어느 쪽으로 시작해도 프로토타입을 만들고 검증한 뒤 제품 전략과 시장 진입 계획으로 이어집니다.`,
    },
    { kind: "heading", id: "four-tabs", text: "화면 네 개" },
    {
      kind: "md",
      md: `| 탭 | 하는 일 |
|---|---|
| 대시보드 | 어디까지 왔는지 본다 — 진행률, 완료한 스테이지, 만들어진 산출물 |
| 워크스페이스 | 실제로 일이 일어나는 곳 — AI와 대화하고 질문에 답한다 |
| 문서 리뷰 | 만들어진 문서를 읽고 승인하거나 수정을 요청한다 |
| 프로토타입 | 명세를 실물로 빌드하고, 호스팅하고, 설문으로 검증한다 |

네 탭은 프로젝트 하나에 속합니다. 프로젝트를 고르지 않으면 눌리지 않습니다.`,
    },
    {
      kind: "callout",
      tone: "tip",
      md: `**정해진 순서를 억지로 따라가지 않아도 됩니다.** 워크플로우가 작업에 적응합니다 —
"이전 단계로 돌아가고 싶어", "이건 건너뛰자" 같은 말을 채팅으로 하면 그대로 됩니다.`,
    },
    {
      kind: "details",
      summary: "AI-PLC 전체에서 Pathfinder가 담당하는 범위",
      md: `AI-PLC는 Discovery → Inception → Construction → Operations로 이어집니다.
Pathfinder는 그중 **Discovery만** 다룹니다. 산출된 Discovery 문서와 \`PROTOTYPE-*.md\`를
개발팀에 넘기면, 그 뒤 단계는 개발자용 워크스페이스에서 진행됩니다.

프로토타입 빌드 기능이 있는 것은 Discovery 안에서 아이디어를 검증하기 위한 것이고,
그 결과물은 프로덕션 코드가 아닙니다.`,
    },
  ],
};
