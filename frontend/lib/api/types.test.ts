import { describe, it, expect } from "vitest";
import type {
  QuestionFile,
  ProjectState,
  AuditEntry,
  AgentEvent,
  TurnResult,
  ProjectSummary,
} from "./types";

describe("api types mirror the backend models", () => {
  it("QuestionFile carries snake_case parse_ok / raw_markdown and option is_other", () => {
    const qf: QuestionFile = {
      name: "strategy-questions.md",
      preamble: null,
      parse_ok: true,
      raw_markdown: null,
      questions: [
        {
          number: 1,
          category: "Positioning",
          text: "포지셔닝?",
          answer: "A",
          options: [
            { letter: "A", text: "Niche", is_other: false, recommended: true },
            { letter: "X", text: "Other", is_other: true, recommended: false },
          ],
        },
      ],
    };
    expect(qf.parse_ok).toBe(true);
    expect(qf.questions[0].options[0].recommended).toBe(true);
    expect(qf.questions[0].options[1].is_other).toBe(true);
  });

  it("ProjectState uses project_type/current_stage and the three stage statuses", () => {
    const st: ProjectState = {
      project_type: "Greenfield",
      current_stage: "Product Strategy",
      stages: [
        { name: "Workspace Detection", status: "completed", note: null },
        { name: "Product Strategy", status: "in_progress", note: null },
        { name: "Go-to-Market", status: "pending", note: null },
      ],
    };
    expect(st.stages.map((s) => s.status)).toEqual(["completed", "in_progress", "pending"]);
  });

  it("AuditEntry / AgentEvent / TurnResult / ProjectSummary shapes", () => {
    const e: AuditEntry = {
      index: 1,
      timestamp: "2026-07-04T00:00:00Z",
      user_input: "ai-plc를 시작하고 싶어",
      ai_response: "Starting…",
      context: "Session start",
    };
    const ev: AgentEvent = { kind: "done", text: null, path: null, payload: null };
    const tr: TurnResult = { events: [ev] };
    const p: ProjectSummary = { project_id: "pilot1", name: "기획전 AI 어시스턴트" };
    expect(e.user_input).toContain("ai-plc");
    expect(tr.events[0].kind).toBe("done");
    expect(p.name).toContain("기획전");
  });
});
