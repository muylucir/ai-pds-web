// Validation-survey API: admin calls (behind the app's auth) and the two
// public token-only calls used by /survey/[token].
import { API_BASE_URL, ApiError } from "./client";
import { apiFetch } from "./http";

export type SurveyQuestionType = "scale" | "choice" | "text";

export interface SurveyQuestion {
  id: string;
  text: string;
  type: SurveyQuestionType;
  options: string[];
  required: boolean;
}

export interface Questionnaire {
  token: string;
  status: "open" | "closed";
  title: string;
  hypothesis: string;
  questions: SurveyQuestion[];
  created_at: string;
  closed_at: string | null;
}

export interface ScaleStat {
  type: "scale";
  n: number;
  mean: number;
  distribution: Record<string, number>;
}
export interface ChoiceStat { type: "choice"; n: number; counts: Record<string, number> }
export interface TextStat { type: "text"; n: number; samples: string[] }
export type Stat = ScaleStat | ChoiceStat | TextStat;

export interface Rollup {
  count: number;
  rebuilt_at: string;
  per_question: Record<string, Stat>;
}

export interface SurveyView {
  questionnaire: Questionnaire;
  url: string;
  rollup: Rollup;
}

export interface PublicSurvey {
  title: string;
  hypothesis: string;
  questions: SurveyQuestion[];
  // 문항이 쓰인 언어. 응답 화면이 이 값으로 그려진다 — 응답자는 외부인이라
  // pf_lang 쿠키가 없고, 문항이 영어인데 화면만 한국어인 것은 더 나쁘다.
  language?: "ko" | "en";
}

export type AnswerValue = string | number;

/** A closed survey is a normal end state, not a failure — the public form
 *  shows a "마감되었습니다" screen rather than an error. */
export class SurveyClosedError extends Error {
  constructor() {
    super("survey closed");
    this.name = "SurveyClosedError";
  }
}

function base(pid: string, slug: string): string {
  return `/projects/${encodeURIComponent(pid)}/prototypes/${encodeURIComponent(slug)}/survey`;
}

export async function createSurvey(pid: string, slug: string): Promise<{
  token: string; url: string; questions: SurveyQuestion[];
}> {
  return (await apiFetch(base(pid, slug), { method: "POST" }))!;
}

export async function getSurvey(pid: string, slug: string): Promise<SurveyView | null> {
  try {
    return (await apiFetch<SurveyView>(base(pid, slug)))!;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export async function closeSurvey(pid: string, slug: string): Promise<void> {
  await apiFetch(`${base(pid, slug)}/close`, { method: "POST" });
}

export function surveyCsvUrl(pid: string, slug: string): string {
  return `${API_BASE_URL}${base(pid, slug)}/responses.csv`;
}

export interface SynthesisResult {
  path: string;
  response_count: number;
}

/** Write the aggregate into the rule's validation-results.md so the PM's
 *  Discovery flow picks it up. Re-runnable: it overwrites with fresh numbers. */
export async function synthesizeSurvey(
  pid: string, slug: string,
): Promise<SynthesisResult> {
  return (await apiFetch<SynthesisResult>(`${base(pid, slug)}/synthesize`, {
    method: "POST",
  }))!;
}

export async function getPublicSurvey(token: string): Promise<PublicSurvey> {
  try {
    return (await apiFetch<PublicSurvey>(`/survey/${encodeURIComponent(token)}`))!;
  } catch (err) {
    if (err instanceof ApiError && err.status === 410) throw new SurveyClosedError();
    throw err;
  }
}

export async function submitPublicSurvey(
  token: string, answers: Record<string, AnswerValue>,
): Promise<void> {
  try {
    await apiFetch(`/survey/${encodeURIComponent(token)}`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 410) throw new SurveyClosedError();
    throw err;
  }
}
