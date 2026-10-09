// 인계 탭 API — 백엔드 routes/handoff.py와 1:1.
//
// 판정·보완·생성은 전부 서버가 한다(aipds/handoff/). 이 모듈은 모양만 옮긴다.
import { API_BASE_URL, ApiError } from "./client";
import { apiFetch } from "./http";
import { CREDENTIALS } from "@/lib/auth";

export type Origin = "A.1" | "A.2" | "A" | "B" | "EP1";
export type SectionStatus = "sourced" | "partial" | "missing" | "derived";
export type ValidationStatus = "validated" | "survey" | "planned" | "none";

/** PRD 섹션의 순서. 서버 handoff/readiness.SECTIONS와 같다. */
export const SECTION_KEYS = [
  "problem", "audience", "goals", "scenarios", "requirements",
  "non_goals", "constraints", "assumptions", "open_questions",
] as const;
export type SectionKey = (typeof SECTION_KEYS)[number];

export interface Section {
  key: SectionKey;
  number: number;
  status: SectionStatus;
  sources: string[];
}

export interface HandoffPrototype {
  id: string;
  spec: string;
  validation: ValidationStatus;
  evidence: string[];
}

export interface AcceptedSuggestion {
  file: string;
  number: number;
  ask: string;
  answer: string;
}

export interface Readiness {
  origin: Origin | null;
  prototypes: HandoffPrototype[];
  sections: Section[];
  ai_defaults: { answered: number; suggested: number; accepted: number; items: AcceptedSuggestion[] };
  discovery_document: string | null;
  broken_references: string[];
  blockers: string[];
}

/** 보완 문항 id. 서버 handoff/supplement.QUESTIONS와 같다 — 문장은 i18n이 갖는다. */
export const SUPPLEMENT_QUESTION_IDS = [
  "problem.evidence", "problem.why_now", "goals.success", "non_goals.list",
  "constraints.rules", "assumptions.must_be_true", "assumptions.failure_reasons",
] as const;
export type SupplementQuestionId = (typeof SUPPLEMENT_QUESTION_IDS)[number];

export interface SupplementAnswer {
  text: string;
  unknown: boolean;
  updated_at: string;
}

export interface SupplementQuestion {
  id: SupplementQuestionId;
  section: SectionKey;
  answer: SupplementAnswer | null;
}

export interface Confirmation {
  key: string;
  file: string;
  number: number;
  ask: string;
  answer: string;
  confirmed_at: string | null;
}

export interface SupplementView {
  questions: SupplementQuestion[];
  superseded: SupplementQuestion[];
  confirmations: Confirmation[];
}

export interface SupplementUpdate {
  answers: Partial<Record<SupplementQuestionId, { text: string; unknown: boolean }>>;
  confirmed: string[];
}

export type PackageStatus = "generating" | "ready" | "failed" | "interrupted";
export type StepStatus = "pending" | "running" | "done" | "failed";
/** 생성 단계, 실행 순서대로. 서버 handoff/package.STEPS와 같다. */
export type StepName = "prd" | "validation" | "scope";

export interface Step {
  name: StepName;
  file: string;
  status: StepStatus;
  started_at: string | null;
  finished_at: string | null;
  /** 지금까지 받은 본문 글자 수. running인데 0이면 첫 출력을 기다리는 중이다. */
  chars: number;
  /** 본문 전에 모델이 생각을 내보내는 중. */
  thinking: boolean;
  error: string | null;
}

export interface Finding {
  file: string;
  line: number;
  term: string;
  kind: "tech" | "vague";
}

export interface Manifest {
  status: PackageStatus;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  origin: Origin | null;
  files: string[];
  steps: Step[];
  findings: Finding[];
  truncated: string[];
}

export interface PackageView {
  manifest: Manifest | null;
  files: Record<string, string>;
  stale: string[];
  supplement_changed: boolean;
  /** 실패·중단된 생성을 끝난 단계는 두고 이어서 할 수 있는가. */
  resumable: boolean;
}

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/handoff`;

export async function getReadiness(pid: string): Promise<Readiness> {
  return (await apiFetch<Readiness>(`${base(pid)}/readiness`))!;
}

export async function getSupplement(pid: string): Promise<SupplementView> {
  return (await apiFetch<SupplementView>(`${base(pid)}/supplement`))!;
}

export async function putSupplement(pid: string, update: SupplementUpdate): Promise<SupplementView> {
  return (await apiFetch<SupplementView>(`${base(pid)}/supplement`, {
    method: "PUT",
    body: JSON.stringify(update),
  }))!;
}

/** 생성을 시작한다. `resume`이면 지난 시도의 끝난 단계를 두고 나머지만 한다 — 원본이 그사이
 *  바뀌었으면 서버가 처음부터 한다. */
export async function startPackage(pid: string, resume = false): Promise<Manifest> {
  return (await apiFetch<Manifest>(`${base(pid)}/package`, {
    method: "POST",
    body: JSON.stringify({ resume }),
  }))!;
}

export async function getPackage(pid: string): Promise<PackageView> {
  return (await apiFetch<PackageView>(`${base(pid)}/package`))!;
}

export async function downloadPackage(pid: string): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}${base(pid)}/package/archive`, { credentials: CREDENTIALS });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.blob();
}
