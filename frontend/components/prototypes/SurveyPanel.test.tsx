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

describe("SurveyPanel — 결과 취합", () => {
  it("synthesizes and reports the path it wrote", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    const synth = vi.spyOn(api, "synthesizeSurvey").mockResolvedValue({
      path: "aiplc-docs/discovery/prototype/survey-aggregate.md",
      response_count: 7,
    });
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /결과 취합/ }));

    await waitFor(() => expect(synth).toHaveBeenCalledWith(PID, SLUG));
    // The PM needs to know WHERE it landed, and that the loop closes in the
    // Discovery chat — the agent turns this aggregate into Step 6's synthesis.
    expect(await screen.findByText(/survey-aggregate\.md/)).toBeInTheDocument();
    expect(screen.getByText(/validation-results\.md/)).toBeInTheDocument();
    // 응답 수가 화면에 있어야 한다. 문구는 딕셔너리가 소유하므로(기본 로케일
    // ko: "7건을 …에 저장했습니다") 개수만 단정한다 — 리터럴 "7건"을 고정하면
    // 문구를 번역 키로 옮길 때마다 이 테스트가 의미 없이 깨진다.
    expect(screen.getByText(/\b7\b/)).toBeInTheDocument();
  });

  it("한국어 안내는 한국어 어순으로 쓴다", async () => {
    // 앞말·뒷말을 영어 어순으로 끼우면 "건을 7 에 저장했습니다. …경로"가 된다.
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    vi.spyOn(api, "synthesizeSurvey").mockResolvedValue({
      path: "aiplc-docs/discovery/prototype/survey-aggregate.md",
      response_count: 7,
    });
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /결과 취합/ }));
    const code = await screen.findByText("aiplc-docs/discovery/prototype/survey-aggregate.md");
    expect(code.parentElement!.textContent).toBe(
      "응답 7건을 aiplc-docs/discovery/prototype/survey-aggregate.md에 저장했습니다.");
  });

  it("취합 뒤 반영 요청 버튼은 집계 경로를 담은 초안으로 워크스페이스에 간다", async () => {
    // 바로 보내지 않고 초안만 채운다 — 사용자가 설문 밖 의견을 덧붙일 수 있게.
    // 경로가 초안에 있어야 프로토타입이 여러 개일 때 에이전트가 맞는 집계를 읽는다.
    const path = `aiplc-docs/discovery/prototypes/${SLUG}/survey-aggregate.md`;
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    vi.spyOn(api, "synthesizeSurvey").mockResolvedValue({ path, response_count: 7 });
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(screen.queryByRole("link", { name: /반영 요청/ })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /결과 취합/ }));

    const link = await screen.findByRole("link", { name: /반영 요청/ });
    const url = new URL(link.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe(`/projects/${PID}/workspace`);
    expect(url.searchParams.get("draft")).toBe(`설문 결과를 반영해 주세요: ${path}`);
  });

  it("offers 취합 while the survey is still open (interim aggregate)", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByRole("button", { name: /결과 취합/ })).toBeEnabled();
  });

  it("offers 취합 after the survey is closed", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue({
      ...OPEN_VIEW,
      questionnaire: { ...OPEN_VIEW.questionnaire, status: "closed",
                       closed_at: "2026-07-26T00:00:00Z" },
    });
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByRole("button", { name: /결과 취합/ })).toBeEnabled();
  });

  it("surfaces a synthesis failure without wedging the button", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    vi.spyOn(api, "synthesizeSurvey").mockRejectedValue(new Error("boom"));
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /결과 취합/ }));
    expect(await screen.findByText(/취합에 실패/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /결과 취합/ })).toBeEnabled();
  });
});
