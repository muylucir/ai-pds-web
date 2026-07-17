import type { ProjectState } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/aiplc-state.md, adjusted to the mid-run
// view in files/ui/01-dashboard.html (Product Strategy in progress). Notes are
// the mockup's stage sub-labels so ported components render realistic copy.
export const projectState: ProjectState = {
  project_type: "Greenfield",
  current_stage: "Product Strategy",
  stages: [
    { name: "Workspace Detection", status: "completed", note: "PROTOTYPE-*.md 없음 · Greenfield 확인" },
    { name: "Discovery Mode Selection", status: "completed", note: "Path A 선택 — 고객 Pain Point에서 시작" },
    { name: "Envision", status: "completed", note: "Working Backwards PR/FAQ 작성 · 모순 1건 해소" },
    { name: "Solution Analysis", status: "completed", note: "단일 솔루션 (Agentic) → Branch A.1 확정" },
    { name: "Prototype & Validation", status: "completed", note: "UI 프로토타입 · 반복 2회 · Validation 스킵" },
    { name: "Product Strategy", status: "in_progress", note: "포지셔닝 · 차별화 · 비즈니스 모델 — 13개 질문 대기" },
    { name: "Go-to-Market", status: "pending", note: "마케팅 전략 · 사내 확산 · 런칭 계획" },
    { name: "Discovery Document", status: "pending", note: "개발자 워크스페이스(Inception) 핸드오프" },
  ],
};
