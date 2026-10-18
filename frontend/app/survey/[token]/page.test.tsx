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
    // The questions ask hypothetically, as in "실제 업무에 도입된다면" (if it were adopted in real work) (survey/builder.py).
    // If the intro asked for "사용해 본 경험" (experience of having used it), the two premises would clash, and respondents would rate
    // completeness while mistaking mock data for real results.
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
  // Respondents are outsiders, so they have no aipds_lang cookie — the layout's Provider becomes ko.
  // Only this page ignores that and uses the survey language.
  it("영어 설문은 영어로 그려진다", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "Validation survey", hypothesis: "H", language: "en",
      questions: QUESTIONS });
    await renderPage();
    expect(await screen.findByRole("button", { name: /Submit/i })).toBeInTheDocument();
    // The intro must be English too — with only the form in English, it is half a screen.
    const intro = await screen.findByTestId("survey-intro");
    expect(intro.textContent).not.toMatch(/[가-힣]/);
  });

  it("영어 설문은 제출 뒤 감사 화면도 영어다", async () => {
    // On submit, the survey drops out of the screen state. Reading the language from there would make only the thank-you
    // screen fall back to Korean.
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "Validation survey", hypothesis: "H", language: "en",
      questions: QUESTIONS });
    vi.spyOn(api, "submitPublicSurvey").mockResolvedValue();
    await renderPage();
    await userEvent.click(await screen.findByRole("radio", { name: "4" }));
    await userEvent.click(screen.getByRole("button", { name: /Submit/i }));
    expect(await screen.findByText(/Thank/i)).toBeInTheDocument();
    expect(document.querySelector("main")!.textContent).not.toMatch(/[가-힣]/);
  });

  it("영어 설문이 제출 순간 마감되면 마감 안내도 영어다", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "Validation survey", hypothesis: "H", language: "en",
      questions: QUESTIONS });
    vi.spyOn(api, "submitPublicSurvey").mockRejectedValue(new api.SurveyClosedError());
    await renderPage();
    await userEvent.click(await screen.findByRole("radio", { name: "4" }));
    await userEvent.click(screen.getByRole("button", { name: /Submit/i }));
    await screen.findByText(/closed/i);
    expect(document.querySelector("main")!.textContent).not.toMatch(/[가-힣]/);
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
