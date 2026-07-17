import type { AuditEntry } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/audit.md (first entries + a couple later).
export const auditEntries: AuditEntry[] = [
  { index: 1, timestamp: "2026-07-04T01:43:19Z", user_input: "ai-plc를 시작하고 싶어", ai_response: "Starting AI-PLC Discovery workflow. Executing Workspace Detection first.", context: "Session start" },
  { index: 3, timestamp: "2026-07-04T01:43:19Z", user_input: "완료 (Discovery Mode Selection Q1: A)", ai_response: "User selected Path A (Start from customer pain points). Proceeding to Envision.", context: "Discovery mode selection" },
  { index: 8, timestamp: "2026-07-04T02:10:00Z", user_input: "정확합니다", ai_response: "Pain Point summary confirmed. Proceeding to PR/FAQ.", context: "Envision — gate" },
  { index: 11, timestamp: "2026-07-04T02:40:00Z", user_input: "완료 (clarification Q1: C)", ai_response: "Contradiction resolved: 30s SLA to be decided during pilot. PR/FAQ gate passed.", context: "Envision — contradiction resolution" },
  { index: 34, timestamp: "2026-07-04T03:30:00Z", user_input: "완료", ai_response: "Generated 13 Product Strategy questions.", context: "Product Strategy" },
];
