import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SurveyPage from "./page";
import * as api from "@/lib/api/surveys";

const QUESTIONS: api.SurveyQuestion[] = [
  { id: "q1", text: "유용했나요?", type: "scale", options: [], required: true },
];

beforeEach(() => vi.restoreAllMocks());

async function renderPage() {
  let result;
  await act(async () => {
    result = render(<SurveyPage params={Promise.resolve({ token: "tok" })} />);
  });
  return result;
}

describe("public survey page", () => {
  it("renders the questionnaire", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "검증 설문", hypothesis: "가설", questions: QUESTIONS });
    await renderPage();
    expect(await screen.findByText("검증 설문")).toBeInTheDocument();
  });

  it("tells the respondent this is a prototype, not a finished product", async () => {
    // 문항은 "실제 업무에 도입된다면"처럼 가정형으로 묻는다(survey/builder.py).
    // 안내문이 "사용해 본 경험"을 요구하면 두 전제가 어긋나고, 응답자는 목
    // 데이터를 실제 결과로 오해한 채 완성도를 평가한다.
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "검증 설문", hypothesis: "가설", questions: QUESTIONS });
    await renderPage();
    const intro = await screen.findByTestId("survey-intro");
    expect(intro.textContent).toMatch(/체험|데모|완성된 제품이 아/);
    expect(intro.textContent).not.toMatch(/사용해 본 경험/);
  });

  it("shows a closed notice for a closed survey", async () => {
    vi.spyOn(api, "getPublicSurvey").mockRejectedValue(new api.SurveyClosedError());
    await renderPage();
    expect(await screen.findByText(/마감/)).toBeInTheDocument();
  });

  it("shows a not-found notice for an unknown token", async () => {
    vi.spyOn(api, "getPublicSurvey").mockRejectedValue(new Error("nope"));
    await renderPage();
    expect(await screen.findByText(/찾을 수 없습니다|오류/)).toBeInTheDocument();
  });

  it("shows a thank-you screen after submitting and hides the form", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "검증 설문", hypothesis: "가설", questions: QUESTIONS });
    const submit = vi.spyOn(api, "submitPublicSurvey").mockResolvedValue();
    await renderPage();
    await userEvent.click(await screen.findByRole("radio", { name: "4" }));
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(await screen.findByText(/감사/)).toBeInTheDocument();
    expect(submit).toHaveBeenCalledWith("tok", { q1: 4 });
    // Form is gone: re-submitting the same response is not offered.
    expect(screen.queryByRole("button", { name: /제출/ })).not.toBeInTheDocument();
  });
});

describe("설문 언어가 화면 언어를 정한다", () => {
  // 응답자는 외부인이라 pf_lang 쿠키가 없다 — layout의 Provider는 ko가 된다.
  // 이 페이지만 그것을 무시하고 설문 언어를 쓴다.
  it("영어 설문은 영어로 그려진다", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "Validation survey", hypothesis: "H", language: "en",
      questions: QUESTIONS });
    await renderPage();
    expect(await screen.findByRole("button", { name: /Submit/i })).toBeInTheDocument();
    // 안내문까지 영어여야 한다 — 폼만 영어면 절반짜리 화면이다.
    const intro = await screen.findByTestId("survey-intro");
    expect(intro.textContent).not.toMatch(/[가-힣]/);
  });

  it("언어를 모르는 설문(구 데이터)은 한국어로 그려진다", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "검증 설문", hypothesis: "가설", questions: QUESTIONS });
    await renderPage();
    expect(await screen.findByRole("button", { name: /제출/ })).toBeInTheDocument();
  });

  it("임의 문자열이 실려 와도 한국어로 떨어진다", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "검증 설문", hypothesis: "가설",
      language: "klingon" as never, questions: QUESTIONS });
    await renderPage();
    expect(await screen.findByRole("button", { name: /제출/ })).toBeInTheDocument();
  });
});
