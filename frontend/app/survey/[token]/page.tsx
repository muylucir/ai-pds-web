"use client";
import { use, useEffect, useState } from "react";
import { SurveyForm } from "@/components/survey/SurveyForm";
import {
  getPublicSurvey, submitPublicSurvey, SurveyClosedError,
  type AnswerValue, type PublicSurvey,
} from "@/lib/api/surveys";

type State =
  | { kind: "loading" }
  | { kind: "ready"; survey: PublicSurvey }
  | { kind: "closed" }
  | { kind: "error" }
  | { kind: "done" };

// Standalone page: no AppHeader, no auth — respondents reach it by token link
// only, and must never see project internals.
export default function SurveyPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    getPublicSurvey(token)
      .then((survey) => { if (alive) setState({ kind: "ready", survey }); })
      .catch((err) => {
        if (!alive) return;
        setState({ kind: err instanceof SurveyClosedError ? "closed" : "error" });
      });
    return () => { alive = false; };
  }, [token]);

  async function handleSubmit(answers: Record<string, AnswerValue>) {
    setSubmitting(true);
    try {
      await submitPublicSurvey(token, answers);
      setState({ kind: "done" });
    } catch (err) {
      setState({ kind: err instanceof SurveyClosedError ? "closed" : "error" });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="max-w-2xl mx-auto px-6 py-10">
      {state.kind === "loading" && <p className="text-sm text-slate-400">불러오는 중…</p>}

      {state.kind === "ready" && (
        <>
          <h1 className="text-xl font-bold text-slate-800 mb-2">{state.survey.title}</h1>
          <p className="text-sm text-slate-500 mb-8">
            프로토타입을 사용해 본 경험을 알려주세요. 응답은 익명으로 수집됩니다.
          </p>
          <SurveyForm questions={state.survey.questions}
                      onSubmit={(a) => void handleSubmit(a)}
                      submitting={submitting} />
        </>
      )}

      {state.kind === "done" && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-6">
          <p className="font-medium text-emerald-800">응답해 주셔서 감사합니다.</p>
          <p className="text-sm text-emerald-700 mt-1">제출이 완료되었습니다.</p>
        </div>
      )}

      {state.kind === "closed" && (
        <div className="rounded-xl border border-slate-200 p-6">
          <p className="font-medium text-slate-700">이 설문은 마감되었습니다.</p>
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-6">
          <p className="font-medium text-rose-800">설문을 찾을 수 없습니다.</p>
          <p className="text-sm text-rose-700 mt-1">링크를 다시 확인해 주세요.</p>
        </div>
      )}
    </main>
  );
}
