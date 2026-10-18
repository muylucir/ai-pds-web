"use client";
import { use, useEffect, useState } from "react";
import { SurveyForm } from "@/components/survey/SurveyForm";
import {
  getPublicSurvey, submitPublicSurvey, SurveyClosedError,
  type AnswerValue, type PublicSurvey,
} from "@/lib/api/surveys";
import { DEFAULT_LOCALE, isLocale, type Locale } from "@/lib/i18n";
import { LocaleProvider, useT } from "@/lib/i18n/provider";

type State =
  | { kind: "loading" }
  | { kind: "ready"; survey: PublicSurvey }
  | { kind: "closed" }
  | { kind: "error" }
  | { kind: "done" };

// Why the body is split into a separate component: this screen's text must be drawn in **the survey
// language**, and that language is set by the LocaleProvider that SurveyPage wraps around it. Calling
// useT() inside the same component reads the value from above the Provider it renders (React context
// only sees ancestors), so the cookie locale would apply, producing a half-baked screen where the
// questions are in English but only the labels are in Korean.
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
          {/* This pairs with the questions asking hypothetically, as in "실제 업무에 도입된다면"
              (if it were adopted in your actual work) (backend/aipds/survey/builder.py). What respondents
              saw is a demo where only the core flow works and the data may be mock, so if the intro asks
              about real-use experience the two premises clash — respondents would rate completeness while
              mistaking mock data for real results, and those scores would become noise rather than a
              signal about the approach. */}
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
  // The survey language is kept separate from state. On submit, state changes to done (or closed if
  // the survey closed at the moment of submission) and survey disappears, so reading the language from
  // state.survey would drop to ko at that moment and an English survey's thank-you text would come out in Korean.
  const [surveyLocale, setSurveyLocale] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    let alive = true;
    getPublicSurvey(token)
      .then((survey) => {
        if (!alive) return;
        if (isLocale(survey.language)) setSurveyLocale(survey.language);
        setState({ kind: "ready", survey });
      })
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

  // Only this page ignores the UI cookie and uses the survey language. Respondents are outsiders, so
  // they have no cookie (the layout's Provider becomes ko), and a screen with English questions but
  // Korean labels is worse for respondents.
  //
  // Surveys with an unknown language (old data) and the loading state fall back to ko — that is the
  // language of every survey from before this feature.

  return (
    <LocaleProvider locale={surveyLocale}>
      <SurveyBody state={state} submitting={submitting}
                  onSubmit={(a) => void handleSubmit(a)} />
    </LocaleProvider>
  );
}
