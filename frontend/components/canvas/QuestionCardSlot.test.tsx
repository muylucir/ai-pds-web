import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { QuestionCardSlot } from "./QuestionCardSlot";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { clarificationQuestions } from "@/test/fixtures/clarificationQuestions";
import { unparsedQuestions } from "@/test/fixtures/unparsedQuestions";

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
const CLAR = "aiplc-docs/discovery/envision/prfaq-clarification-questions.md";
const UNPARSED = "aiplc-docs/discovery/go-to-market/gtm-questions.md";

describe("QuestionCardSlot", () => {
  it("renders QuestionSummaryCard when every question in the fetched file is answered", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={STRAT} onChoose={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText(/13개 답변 완료/)).toBeInTheDocument();
  });

  it("renders ClarificationCard when the file has an unanswered clarification question", async () => {
    const unanswered = {
      ...clarificationQuestions,
      questions: clarificationQuestions.questions.map((q) => ({ ...q, answer: null })),
    };
    server.use(http.get(`${API_BASE_URL}/projects/pilot1/questions/${CLAR}`, () => HttpResponse.json(unanswered)));
    const onChoose = vi.fn();
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={CLAR} onChoose={onChoose} busy={false} />);
    });
    expect(await screen.findByText("답변 간 모순 감지 — 게이트 보류")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /아직 정하지 않음/ }));
    expect(onChoose).toHaveBeenCalledWith("C — 아직 정하지 않음 — 파일럿 운영 중 데이터로 결정");
  });

  it("renders a compact link card for an unanswered non-clarification / unparsed file", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${UNPARSED}`, () => HttpResponse.json(unparsedQuestions)),
    );
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={UNPARSED} onChoose={vi.fn()} busy={false} />);
    });
    const link = await screen.findByRole("link", { name: /질문 답변하러 가기/ });
    expect(link.getAttribute("href")).toContain(encodeURIComponent(UNPARSED));
  });

  it("renders a Korean error line when the fetch fails", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={STRAT} onChoose={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText("질문을 불러오지 못했습니다.")).toBeInTheDocument();
  });
});
