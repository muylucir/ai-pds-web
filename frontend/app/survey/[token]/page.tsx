"use client";
import { use, useEffect, useState } from "react";
import { SurveyForm } from "@/components/survey/SurveyForm";
import {
  getPublicSurvey, submitPublicSurvey, SurveyClosedError,
  type AnswerValue, type PublicSurvey,
} from "@/lib/api/surveys";
import { DEFAULT_LOCALE, isLocale } from "@/lib/i18n";
import { LocaleProvider, useT } from "@/lib/i18n/provider";

type State =
  | { kind: "loading" }
  | { kind: "ready"; survey: PublicSurvey }
  | { kind: "closed" }
  | { kind: "error" }
  | { kind: "done" };

// 본문을 별도 컴포넌트로 나눈 이유: 이 화면의 문구는 **설문 언어**로 그려야
// 하고, 그 언어는 SurveyPage가 씌우는 LocaleProvider가 정한다. 같은 컴포넌트
// 안에서 useT()를 부르면 자기가 렌더하는 Provider보다 위에서 값을 읽으므로
// (React 컨텍스트는 조상만 본다) 쿠키 로케일이 걸려, 문항은 영어인데 라벨만
// 한국어인 절반짜리 화면이 된다.
function SurveyBody({
  state,
  submitting,
  onSubmit,
}: {
  state: State;
  submitting: boolean;
  onSubmit: (answers: Record<string, AnswerValue>) => void;
}) {
  const t = useT();
  return (
    <main className="max-w-2xl mx-auto px-6 py-10">
      {state.kind === "loading" && <p className="text-sm text-slate-400">{t("sp.loading")}</p>}

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
            {t("sp.introBodyPrefix")}{" "}
            <strong className="font-semibold text-slate-600">{t("sp.introBodyBold")}</strong>
            {t("sp.introBodySuffix")}
          </p>
          <SurveyForm questions={state.survey.questions}
                      onSubmit={onSubmit}
                      submitting={submitting} />
        </>
      )}

      {state.kind === "done" && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-6">
          <p className="font-medium text-emerald-800">{t("sp.thanks")}</p>
          <p className="text-sm text-emerald-700 mt-1">{t("sp.submitted")}</p>
        </div>
      )}

      {state.kind === "closed" && (
        <div className="rounded-xl border border-slate-200 p-6">
          <p className="font-medium text-slate-700">{t("sp.closed")}</p>
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-6">
          <p className="font-medium text-rose-800">{t("sp.notFound")}</p>
          <p className="text-sm text-rose-700 mt-1">{t("sp.checkLink")}</p>
        </div>
      )}
    </main>
  );
}

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

  // 이 페이지만 UI 쿠키를 무시하고 설문 언어를 쓴다. 응답자는 외부인이라
  // 쿠키가 없고(layout의 Provider는 ko가 된다), 문항이 영어인데 라벨만
  // 한국어인 화면은 응답자에게 더 나쁘다.
  //
  // 언어를 모르는 설문(구 데이터)과 로딩 중에는 ko로 떨어진다 — 그것이 이
  // 기능 이전 모든 설문의 언어다.
  const surveyLocale =
    state.kind === "ready" && isLocale(state.survey.language)
      ? state.survey.language
      : DEFAULT_LOCALE;

  return (
    <LocaleProvider locale={surveyLocale}>
      <SurveyBody state={state} submitting={submitting}
                  onSubmit={(a) => void handleSubmit(a)} />
    </LocaleProvider>
  );
}
