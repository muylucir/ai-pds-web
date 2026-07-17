import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { unparsedQuestions } from "@/test/fixtures/unparsedQuestions";
import { clarificationQuestions } from "@/test/fixtures/clarificationQuestions";
import QuestionsPage from "./page";

// next/navigation is not wired in the test renderer; stub the hooks the page uses.
// `mockSearch` is mutated per-test to simulate the `?file=` param a client-side
// nav (e.g. the ClarificationBanner's next/link) would set without remounting
// the page — exactly the scenario Fix 1 (key={active}) guards against.
let mockSearch = new URLSearchParams("");
vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearch,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
const CLAR = "aiplc-docs/discovery/envision/prfaq-clarification-questions.md";

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

  // Regression for the stale-state bug: the ClarificationBanner links via
  // next/link (client-side nav) which changes `?file=` WITHOUT remounting the
  // page. Before the fix, QuestionForm's answers map — seeded once via a lazy
  // useState initializer — survived the file switch, so the clarification
  // file's form showed the PREVIOUS file's selected options. The fix has two
  // parts: (1) `key={active}` on <QuestionForm>/<RawMarkdownFallback> forces
  // a remount (and thus a re-seed) whenever the active file changes; (2) the
  // page tags each `getQuestionFile` result with the `active` path it was
  // fetched for and only renders once the loaded data's path matches the
  // CURRENT `active` — because `useAsync` keeps the previous file's `data`
  // around while the new fetch for the new `active` is still in flight, and
  // without this guard the fresh-keyed remount would seed itself from that
  // stale, previous-file data for one render.
  it("re-seeds QuestionForm from the clarification file's own answers after a client-side file switch (stale-state regression)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () =>
        HttpResponse.json({ questions: [STRAT, CLAR] }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${CLAR}`, () => HttpResponse.json(clarificationQuestions)),
    );

    // Start with no ?file= param → defaults to the first non-clarification
    // file (STRAT), whose Q1 answer is "A".
    mockSearch = new URLSearchParams("");
    let rerender!: ReturnType<typeof render>["rerender"];
    await act(async () => {
      ({ rerender } = render(<QuestionsPage params={params} />));
    });

    await screen.findByText(/Q1\. 이 제품을 시장/);
    expect(
      screen.getByRole("radio", { checked: true, name: /사내 특화 전문 도구/ }),
    ).toBeInTheDocument();

    // Simulate the ClarificationBanner's client-side navigation: the search
    // param changes to the clarification file, but the page component itself
    // is NOT unmounted (this is what next/link + the App Router do — no full
    // remount on a same-route search-param change). We mutate the mocked
    // useSearchParams() return value and re-render the SAME element tree.
    mockSearch = new URLSearchParams(`file=${encodeURIComponent(CLAR)}`);
    await act(async () => {
      rerender(<QuestionsPage params={params} />);
    });

    // The clarification file's own question must be shown, seeded from ITS
    // OWN answer ("C" = "아직 정하지 않음 — 파일럿 운영 중 데이터로 결정") —
    // not a leftover "A" answer carried over from strategy-questions.md's Q1.
    await screen.findByText(/응답 시간 목표\(SLA\)는 어떻게/);
    expect(
      screen.getByRole("radio", { checked: true, name: /아직 정하지 않음/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("radio", { checked: true, name: /30초 이내 응답 목표는 그대로 유지/ }),
    ).not.toBeInTheDocument();
  });

  it("shows a generic load-error state when listing question files fails (non-404)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<QuestionsPage params={params} />);
    });
    expect(
      await screen.findByText(/질문 목록을 불러오지 못했습니다. 백엔드 연결을 확인하세요\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/아직 답변할 질문이 없습니다/)).not.toBeInTheDocument();
  });

  it("shows a generic load-error state when a non-404 error occurs loading the active question file", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () => HttpResponse.json({ questions: [STRAT] })),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<QuestionsPage params={params} />);
    });
    expect(
      await screen.findByText(/질문을 불러오지 못했습니다. 백엔드 연결을 확인하세요\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/질문 파일을 찾을 수 없습니다\./)).not.toBeInTheDocument();
  });
});
