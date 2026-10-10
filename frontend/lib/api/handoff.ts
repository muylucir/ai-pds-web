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

/** 질문 파일이 속한 Discovery 단계. 서버 handoff/readiness.Stage와 같다. */
export type Stage =
  | "envision" | "solution_analysis" | "use_case_intake" | "prioritization"
  | "prototype" | "product_strategy" | "go_to_market" | "other";
export const STAGE_ORDER: Stage[] = [
  "envision", "solution_analysis", "use_case_intake", "prioritization",
  "prototype", "product_strategy", "go_to_market", "other",
];

export interface Confirmation {
  key: string;
  file: string;
  number: number;
  ask: string;
  /** 답의 원문(`"A"`, `"A: 부연"`, `"A,C"`). 화면에는 `choices`를 보인다. */
  answer: string;
  stage: Stage;
  /** 고른 보기의 문장 — PM이 확인하는 대상이다. */
  choices: string[];
  /** 보기의 `←` 뒤에 AI가 붙인 메모. */
  note: string;
  /** `"A: 부연"`의 부연. */
  remark: string;
  confirmed_at: string | null;
  /** 이 결정이 된 PRD 항목들. 비어 있으면 PRD가 이 결정을 "AI 제안 수락"으로 인용하지 않았다. */
  in_prd: string[];
  /** PRD 3번(목표와 성공 지표)이 이 결정을 인용했다. */
  in_goals: boolean;
}

export interface OpenAnswer extends SupplementAnswer {
  question: string;
}

/** PRD 9번(열린 질문)에서 PM이 답할 것으로 적힌 질문. 서버 handoff/supplement.OpenQuestionView. */
export interface OpenQuestion {
  /** `open:` + 질문 문장 해시. 저장할 때 그대로 돌려준다. */
  key: string;
  /** 지금 PRD의 번호(O-03). 앞서 답해 PRD에서 빠진 질문은 비어 있다. */
  id: string;
  question: string;
  answer: OpenAnswer | null;
}

export interface SupplementView {
  /** 마지막 패키지가 있어서 `in_prd`를 믿을 수 있는가. */
  has_package: boolean;
  questions: SupplementQuestion[];
  superseded: SupplementQuestion[];
  confirmations: Confirmation[];
  /** 마지막 PRD의 열린 질문 중 PM 몫. 패키지가 없으면 비어 있다. */
  open_questions: OpenQuestion[];
  /** 답했는데 지금 PRD의 열린 질문에는 없는 것 — 반영됐거나 문장이 바뀌었다. */
  open_answered: OpenQuestion[];
}

export interface SupplementUpdate {
  answers: Partial<Record<SupplementQuestionId, { text: string; unknown: boolean }>>;
  confirmed: string[];
  open_answers: Record<string, { question: string; text: string; unknown: boolean }>;
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
  kind: FindingKind;
}

/** 서버 handoff/package.Finding.kind. 화면의 검사 목록 순서이기도 하다. */
export const FINDING_KINDS = ["tech", "vague", "acceptance", "grade", "ai_goal", "internal"] as const;
export type FindingKind = (typeof FINDING_KINDS)[number];

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
