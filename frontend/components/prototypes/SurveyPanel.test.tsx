import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SurveyPanel } from "./SurveyPanel";
import * as api from "@/lib/api/surveys";

const PID = "p1";
const SLUG = "demo";
const QUESTIONS: api.SurveyQuestion[] = [
  { id: "q1", text: "유용?", type: "scale", options: [], required: true },
];
const OPEN_VIEW: api.SurveyView = {
  questionnaire: { token: "tok", status: "open", title: "검증 설문",
                   hypothesis: "가설", questions: QUESTIONS,
                   created_at: "x", closed_at: null },
  url: "/survey/tok",
  rollup: { count: 0, rebuilt_at: "x", per_question: {
    q1: { type: "scale", n: 0, mean: 0,
          distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 } } } },
};

beforeEach(() => vi.restoreAllMocks());

describe("SurveyPanel", () => {
  it("offers generation when no survey exists", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(null);
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByRole("button", { name: /질문 생성/ })).toBeInTheDocument();
  });

  it("creates a survey and reloads the view", async () => {
    const getSurvey = vi.spyOn(api, "getSurvey")
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(OPEN_VIEW);
    const createSurvey = vi.spyOn(api, "createSurvey")
      .mockResolvedValue({ token: "tok", url: "/survey/tok", questions: QUESTIONS });

    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /질문 생성/ }));

    await waitFor(() => expect(createSurvey).toHaveBeenCalledWith(PID, SLUG));
    expect(getSurvey).toHaveBeenCalledTimes(2);
  });

  it("shows the public link for an open survey", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByText(/\/survey\/tok/)).toBeInTheDocument();
  });

  it("closes the survey", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    const closeSurvey = vi.spyOn(api, "closeSurvey").mockResolvedValue();
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /설문 마감/ }));
    await waitFor(() => expect(closeSurvey).toHaveBeenCalledWith(PID, SLUG));
  });

  it("offers CSV export and regeneration once closed", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue({
      ...OPEN_VIEW,
      questionnaire: { ...OPEN_VIEW.questionnaire, status: "closed",
                       closed_at: "2026-07-26T00:00:00Z" },
    });
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByRole("link", { name: /CSV/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /새 설문 생성/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /설문 마감/ })).not.toBeInTheDocument();
  });

  it("surfaces a generation failure without wedging the panel", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(null);
    vi.spyOn(api, "createSurvey").mockRejectedValue(new Error("boom"));
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /질문 생성/ }));
    expect(await screen.findByText(/실패/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /질문 생성/ })).toBeEnabled();
  });
});
