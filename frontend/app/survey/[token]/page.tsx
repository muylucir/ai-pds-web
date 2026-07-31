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
          {/* 문항이 "실제 업무에 도입된다면"처럼 가정형으로 묻는 것과 짝이다
              (backend/pathfinder/survey/builder.py). 응답자가 본 것은 핵심
              흐름만 동작하는 데모이고 데이터는 목일 수 있는데, 안내문이 실사용
              경험을 요구하면 두 전제가 어긋난다 — 목 데이터를 실제 결과로
              오해한 채 완성도를 평가하게 되고, 그 점수는 접근에 대한 신호가
              아니라 잡음이 된다. */}
          <p data-testid="survey-intro" className="text-sm text-slate-500 mb-8">
            체험하신 프로토타입에 대한 의견을 알려주세요. 완성된 제품이 아니라
            아이디어를 검증하기 위한 데모이므로, 화면의 완성도나 데이터의
            정확성보다 <strong className="font-semibold text-slate-600">접근 방향이
            맞는지</strong>를 중심으로 답해 주시면 됩니다. 사용해 보지 않은
            기능은 그대로 표시해 주세요. 응답은 익명으로 수집됩니다.
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
