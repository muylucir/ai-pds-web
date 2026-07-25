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
