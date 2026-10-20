// Handoff tab API — 1:1 with backend routes/handoff.py.
//
// Judging, supplementing, and generating are all done by the server (aipds/handoff/). This module
// only carries the shapes.
import { API_BASE_URL, ApiError } from "./client";
import { apiFetch } from "./http";
import type { QuestionOption } from "./types";
import { CREDENTIALS } from "@/lib/auth";

export type Origin = "A.1" | "A.2" | "A" | "B" | "EP1";
export type SectionStatus = "sourced" | "partial" | "missing" | "derived";
export type ValidationStatus = "validated" | "survey" | "planned" | "none";

/** The order of PRD sections. Same as the server's handoff/readiness.SECTIONS. */
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

/** Whether the spec of record reflects the build sessions' changes (change history) — backend
 *  handoff/readiness.ReconcileStatus. */
export type ReconcileStatus = "not_needed" | "current" | "missing" | "stale";

export interface HandoffPrototype {
  id: string;
  spec: string;
  validation: ValidationStatus;
  evidence: string[];
  reconcile: ReconcileStatus;
  /** The change history file. null if absent. */
  history: string | null;
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

/** Supplementary question id. Same as the server's handoff/supplement.QUESTIONS — i18n holds the
 *  sentences. */
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

/** The Discovery stage a question file belongs to. Same as the server's handoff/readiness.Stage. */
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
  /** The raw answer (`"A"`, `"A: 부연"`, `"A,C"`). The screen shows `choices`. */
  answer: string;
  stage: Stage;
  /** The sentences of the chosen options — what the PM confirms. */
  choices: string[];
  /** The note the AI attached after an option's `←`. */
  note: string;
  /** The elaboration in `"A: 부연"` (A: elaboration). */
  remark: string;
  confirmed_at: string | null;
  /** The vision.md lines that rely on this decision as an unconfirmed AI suggestion. Empty if vision.md does not (or there
   *  is no package yet). */
  in_vision: string[];
  /** One of those lines is in the first-release scope or the success metrics. */
  in_scope: boolean;
  /** The question's options — the tab asks it again with these when the PM changes the answer. */
  options: QuestionOption[];
  multi_select: boolean;
}

export interface OpenAnswer extends SupplementAnswer {
  question: string;
  /** Who an external confirmation question is currently being checked with. */
  pending_with: string;
}

/** An option of a pre-build decision. Only options grounded in the source come, without a
 *  recommendation (server package.parse_options). */
export interface OpenOption {
  text: string;
  /** The basis — a path relative to `discovery/` (down to the question number for a question
   *  file). */
  source: string;
}

/** A question in the open decisions written as one for the PM to answer. Server
 *  handoff/supplement.OpenQuestionView. */
export interface OpenQuestion {
  /** `open:` + the question sentence hash. Returned as is when saving. */
  key: string;
  /** The number in the current edition (O-03). Empty for a question that was answered earlier and
   *  dropped out of the open decisions. */
  id: string;
  question: string;
  answer: OpenAnswer | null;
  /** How a build-time decision is made until an answer comes in (the "until answered" column). */
  until: string;
  /** The person to answer is "PM(외부 확인)" (PM, external confirmation) — a fact to obtain, not a
   *  decision to choose. There are no options. */
  external: boolean;
  /** If empty, it is asked as free text. */
  options: OpenOption[];
}

export interface SupplementView {
  /** Whether there is a last package. If not, the open decisions cannot be read yet. */
  has_package: boolean;
  questions: SupplementQuestion[];
  superseded: SupplementQuestion[];
  confirmations: Confirmation[];
  /** Among the last open decisions, those that are the PM's to answer and due before the build.
   *  Empty if there is no package. */
  open_questions: OpenQuestion[];
  /** The PM's to answer, but fine to settle during the build. Collapsed, and may be answered. */
  open_deferred: OpenQuestion[];
  /** Answered, but not in the current open decisions — either applied or the sentence changed. */
  open_answered: OpenQuestion[];
}

export interface SupplementUpdate {
  answers: Partial<Record<SupplementQuestionId, { text: string; unknown: boolean }>>;
  confirmed: string[];
  open_answers: Record<string, { question: string; text: string; unknown: boolean; pending_with?: string }>;
}

export type PackageStatus = "generating" | "ready" | "failed" | "interrupted";
export type StepStatus = "pending" | "running" | "done" | "failed";
/** Generation steps, in execution order. Same as the server's handoff/package.STEPS. */
export type StepName = "vision" | "options";

export interface Step {
  name: StepName;
  file: string;
  status: StepStatus;
  started_at: string | null;
  finished_at: string | null;
  /** The number of body characters received so far. If running and 0, it is waiting for the
   *  first output. */
  chars: number;
  /** The model is emitting thinking before the body. */
  thinking: boolean;
  error: string | null;
}

export interface Finding {
  file: string;
  line: number;
  term: string;
  /** One of `FindingKind`. Packages from an earlier configuration may still have check kinds that
   *  no longer exist. */
  kind: FindingKind | (string & {});
}

/** Server handoff/package.Finding.kind. Also the order of the check list on screen. */
export const FINDING_KINDS = [
  "structure", "tech", "vague", "triage", "citation", "dropped", "internal",
] as const;
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
  /** Whether a failed or interrupted generation can be continued, keeping the finished steps. */
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

/** Change a settled answer. The server writes it to the question file's `[Answer]:` tag and starts the turn in which the agent
 *  carries the change through the documents; the workspace attaches to it as it opens (same as approval). */
export async function reviseAnswer(pid: string, file: string, number: number, answer: string): Promise<{ turnId: string }> {
  const r = (await apiFetch<{ turn_id: string }>(`${base(pid)}/revise`, {
    method: "POST",
    body: JSON.stringify({ file, number, answer }),
  }))!;
  return { turnId: r.turn_id };
}

/** Start generation. With `resume`, the finished steps of the last attempt are kept and only the
 *  rest is done — if the source changed in the meantime, the server starts from scratch. */
export async function startPackage(pid: string, resume = false): Promise<Manifest> {
  return (await apiFetch<Manifest>(`${base(pid)}/package`, {
    method: "POST",
    body: JSON.stringify({ resume }),
  }))!;
}

export async function getPackage(pid: string): Promise<PackageView> {
  return (await apiFetch<PackageView>(`${base(pid)}/package`))!;
}

/** The zip name and the name of the top-level folder inside it (without `.zip`). Same rule as the
 *  server's package.archive_folder — the start prompt's paths point to this folder, so the saved
 *  file name must match too. */
export function archiveFolder(pid: string): string {
  const safe = pid.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "project";
  return `${safe}-handoff`;
}

export async function downloadPackage(pid: string): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}${base(pid)}/package/archive`, { credentials: CREDENTIALS });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.blob();
}

/** Fully reset the handoff tab — the package, earlier editions, supplementary answers, and AI
 *  suggestion confirmations. Discovery artifacts stay as they are. If generation is running, the
 *  server rejects with 409. */
export async function resetHandoff(pid: string): Promise<{ deleted: number }> {
  return (await apiFetch<{ deleted: number }>(base(pid), { method: "DELETE" }))!;
}
