import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { unparsedQuestions } from "@/test/fixtures/unparsedQuestions";
import QuestionsPage from "./page";

// next/navigation is not wired in the test renderer; stub the hooks the page uses.
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";

const params = Promise.resolve({ projectId: "pilot1" });

describe("Questions page", () => {
  it("defaults to the first question file and renders the form", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () => HttpResponse.json({ questions: [STRAT] })),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    // `use(params)` suspends on the first render because the test's plain
    // Promise.resolve(...) params (unlike Next's internally-tracked params
    // thenable) isn't pre-marked as settled. Wrapping the initial render in
    // act() lets that Suspense retry flush before we start querying/waiting.
    await act(async () => {
      render(<QuestionsPage params={params} />);
    });
    expect(await screen.findByText(/Q1\. 이 제품을 시장/)).toBeInTheDocument();
  });

  it("PUTs answers on submit and re-renders the reparsed file", async () => {
    let putBody: any;
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () => HttpResponse.json({ questions: [STRAT] })),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
      http.put(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json(strategyQuestions);
      }),
    );
    await act(async () => {
      render(<QuestionsPage params={params} />);
    });
    await screen.findByText(/Q1\. 이 제품을 시장/);
    await userEvent.click(screen.getByRole("button", { name: /답변 제출/ }));
    await waitFor(() => expect(putBody).toBeTruthy());
    expect(putBody.answers["1"]).toBe("A");
  });

  it("renders the clarification banner when a clarification file exists", async () => {
    const CLAR = "aiplc-docs/discovery/envision/prfaq-clarification-questions.md";
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () =>
        HttpResponse.json({ questions: [STRAT, CLAR] }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    await act(async () => {
      render(<QuestionsPage params={params} />);
    });
    expect(await screen.findByText(/모순이 감지되어 게이트가 보류/)).toBeInTheDocument();
  });

  it("falls back to raw markdown when parse_ok is false", async () => {
    const FREE = "aiplc-docs/freeform-notes.md";
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () => HttpResponse.json({ questions: [FREE] })),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${FREE}`, () => HttpResponse.json(unparsedQuestions)),
    );
    await act(async () => {
      render(<QuestionsPage params={params} />);
    });
    expect(await screen.findByText(/표준 형식으로 파싱하지 못했습니다/)).toBeInTheDocument();
  });
});
