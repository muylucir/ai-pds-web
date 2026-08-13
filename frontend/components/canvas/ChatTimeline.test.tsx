// frontend/components/canvas/ChatTimeline.test.tsx  (full replacement)
import { describe, it, expect, vi } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ChatTimeline } from "./ChatTimeline";
import type { ChatTimelineItem } from "./ChatTimeline";
import type { ChatItem } from "@/lib/useTurnStream";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { LocaleProvider } from "@/lib/i18n/provider";

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
const DOC = "aiplc-docs/discovery/discovery-document.md";

// Shared render helper for the stick-to-bottom suite below: a thin wrapper
// over ChatTimeline with sensible defaults so each test only varies `items`
// and `stickSignal`.
function Harness({ items, stickSignal }: { items: ChatTimelineItem[]; stickSignal?: number }) {
  return (
    <ChatTimeline
      items={items}
      projectId="pilot1"
      onChoose={vi.fn()}
      onOpenArtifact={vi.fn()}
      busy={false}
      stickSignal={stickSignal}
    />
  );
}

let msgCounter = 0;
function msg(text: string): ChatItem {
  return { id: `m-${msgCounter++}`, role: "user", text };
}

describe("ChatTimeline", () => {
  it("renders user and AI bubbles in order", () => {
    const items: ChatItem[] = [
      { id: "u1", role: "user", text: "필터 추가해줘" },
      { id: "a1", role: "ai", text: "추가했습니다.", trace: [], streaming: false, error: null },
    ];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText("필터 추가해줘")).toBeInTheDocument();
    expect(screen.getByText("추가했습니다.")).toBeInTheDocument();
  });

  it("renders an empty state with no items", () => {
    render(<ChatTimeline items={[]} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />);
    expect(screen.getByText(/대화를 시작해 보세요/)).toBeInTheDocument();
  });

  it("히스토리 로딩 중에는 빈 상태 문구를 숨긴다 (부모가 그리는 스켈레톤과 겹치지 않도록)", () => {
    render(
      <ChatTimeline
        items={[]}
        projectId="pilot1"
        onChoose={vi.fn()}
        onOpenArtifact={vi.fn()}
        busy={false}
        historyLoading
      />,
    );
    expect(screen.queryByText(/대화를 시작해 보세요/)).not.toBeInTheDocument();
  });

  it("renders the verbatim typing-hint chrome once there is at least one item, but not on the empty state", () => {
    const items: ChatItem[] = [{ id: "u1", role: "user", text: "필터 추가해줘" }];
    const { rerender } = render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText(/버튼 대신 채팅으로 답해도 됩니다/)).toBeInTheDocument();
    rerender(<ChatTimeline items={[]} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />);
    expect(screen.queryByText(/버튼 대신 채팅으로 답해도 됩니다/)).not.toBeInTheDocument();
  });

  it("renders a questions card item via QuestionCardSlot", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    const items: ChatItem[] = [{ id: "c1", role: "card", card: "questions", path: STRAT }];
    await act(async () => {
      render(
        <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
      );
    });
    expect(await screen.findByText(/13개 답변 완료/)).toBeInTheDocument();
  });

  it("renders an artifact card item that calls onOpenArtifact when clicked", async () => {
    const onOpenArtifact = vi.fn();
    const items: ChatItem[] = [{ id: "c2", role: "card", card: "artifact", path: DOC }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={onOpenArtifact} busy={false} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /우측 패널에서 열기/ }));
    expect(onOpenArtifact).toHaveBeenCalledTimes(1);
  });

  it("renders a history-card item (from useWorkspaceStream) as a static summary, naming the questions file", () => {
    const items: ChatTimelineItem[] = [{ id: "h1", role: "history-card", name: "mode-selection" }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText(/질문지 제시됨/)).toBeInTheDocument();
    expect(screen.getByText(/mode-selection/)).toBeInTheDocument();
  });

  it("renders a history-card item with no name without the trailing dash", () => {
    const items: ChatTimelineItem[] = [{ id: "h2", role: "history-card", name: null }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText("📋 질문지 제시됨")).toBeInTheDocument();
  });

  it("복원된 질문 payload가 있으면 문항 수를 보여주고 펼쳐서 질문·보기를 읽는다", async () => {
    // 종전에는 "질문지 제시됨" 한 줄뿐이어서 스크롤백에서 무엇을 물었는지 알
    // 수 없었다 — payload는 트랜스크립트의 tool_use.input에 구조화된 채로
    // 남아 있는데 복원 코드가 버리고 있었다.
    const items: ChatTimelineItem[] = [{
      id: "h3", role: "history-card", name: "discovery-questions",
      file: {
        name: "discovery-questions", preamble: null, parse_ok: true,
        raw_markdown: null,
        questions: [{
          number: 1, category: null, text: "무엇을 만드나요?", answer: null,
          multi_select: false,
          options: [
            { letter: "A", text: "자동차 부품", is_other: false, recommended: false },
            { letter: "X", text: "Other", is_other: true, recommended: false },
          ],
        }],
      } as never,
    }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText(/1문항/)).toBeInTheDocument();
    // 접힌 상태에서는 질문이 보이지 않는다(카드가 타임라인을 잡아먹지 않게).
    expect(screen.queryByText(/무엇을 만드나요\?/)).toBeNull();

    await userEvent.setup().click(screen.getByRole("button", { name: "펼치기" }));

    expect(screen.getByText(/Q1\. 무엇을 만드나요\?/)).toBeInTheDocument();
    expect(screen.getByText("A. 자동차 부품")).toBeInTheDocument();
    // is_other는 자유 입력 자리표시자다 — 제시된 보기가 아니므로 나열하지 않는다.
    expect(screen.queryByText(/X\. Other/)).toBeNull();
  });
});

describe("ChatTimeline — stick-to-bottom", () => {
  function scroller(): HTMLElement {
    return screen.getByLabelText("대화 타임라인");
  }
  function fakeScrollGeometry(el: HTMLElement, { height = 400, content = 1000 }) {
    Object.defineProperty(el, "clientHeight", { value: height, configurable: true });
    Object.defineProperty(el, "scrollHeight", { value: content, configurable: true });
  }

  it("items가 추가되면 바닥으로 스크롤한다 (기본 stick)", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={0} />);
    expect(el.scrollTop).toBe(el.scrollHeight);
  });

  it("사용자가 위로 스크롤하면 자동 스크롤이 멈춘다", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    // 사용자가 위로: scrollTop을 바닥에서 멀리 두고 scroll 이벤트 발생
    el.scrollTop = 100;
    fireEvent.scroll(el);
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={0} />);
    expect(el.scrollTop).toBe(100); // 위치 보존 — 끌려 내려가지 않음
  });

  it("stickSignal이 증가하면(메시지 전송) 무조건 바닥으로 복귀한다", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    el.scrollTop = 100;
    fireEvent.scroll(el);
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={1} />);
    expect(el.scrollTop).toBe(el.scrollHeight);
  });
});

describe("답변 제출 말풍선", () => {
  // answers는 UserItem에 실려 온다(useWorkspaceStream.historyItemToChatItem이
  // GET /history의 HistoryItem.answers를 그대로 옮긴다).
  const answerItem: ChatTimelineItem = {
    id: "a1",
    role: "user",
    text: "답변 제출 — 1: A · 2: B",
    answers: { "1": "A", "2": "B" },
  };

  it("answers가 있으면 UI 언어로 문구를 만든다", () => {
    render(
      <LocaleProvider locale="en">
        <Harness items={[answerItem]} />
      </LocaleProvider>,
    );
    // 백엔드가 실어 보낸 한국어 text는 무시하고 UI 언어로 다시 만든다.
    expect(screen.getByText(/Answers submitted/)).toBeInTheDocument();
    expect(screen.getByText(/1: A · 2: B/)).toBeInTheDocument();
    expect(screen.queryByText(/답변 제출/)).toBeNull();
  });

  it("한국어 UI에서는 한국어 문구다", () => {
    render(
      <LocaleProvider locale="ko">
        <Harness items={[answerItem]} />
      </LocaleProvider>,
    );
    expect(screen.getByText(/답변 제출/)).toBeInTheDocument();
  });

  // 이 조합(answers + questions)이 답변 레코드가 있는 세션에서 온다. 종전에는
  // 복원된 말풍선이 `답변 제출: Your questions have been answered: "질문"="라벨"`
  // 한 줄이었다 — CLI가 쓴 영어 문장을 백엔드가 펼 수 없어 그대로 실어 보냈고,
  // 문항 번호·보기 letter·보기 텍스트가 전부 사라졌다.
  const questionFile = {
    name: "discovery-questions",
    preamble: null,
    parse_ok: true,
    raw_markdown: null,
    questions: [
      {
        number: 1, category: "도메인", text: "무엇을 만드나요?", answer: null,
        multi_select: false,
        options: [
          { letter: "A", text: "자동차 부품 — IATF 16949 환경", is_other: false, recommended: false },
          { letter: "B", text: "가전", is_other: false, recommended: false },
          { letter: "X", text: "Other", is_other: true, recommended: false },
        ],
      },
      {
        number: 2, category: "규모", text: "월 클레임 건수는?", answer: null,
        multi_select: true,
        options: [
          { letter: "A", text: "10건 미만", is_other: false, recommended: false },
          { letter: "B", text: "10~30건", is_other: false, recommended: false },
        ],
      },
    ],
  };

  it("questions가 함께 오면 라이브와 같은 문구(질문 + 보기 텍스트)를 만든다", () => {
    render(
      <LocaleProvider locale="ko">
        <Harness items={[{ ...answerItem, answers: { "1": "A", "2": "A,B" },
                           questions: questionFile as never }]} />
      </LocaleProvider>,
    );
    // 문항 번호와 질문 원문이 보인다.
    expect(screen.getByText(/Q1\. 무엇을 만드나요\?/)).toBeInTheDocument();
    // letter만이 아니라 보기 텍스트로 펼쳐진다.
    expect(screen.getByText(/A\. 자동차 부품 — IATF 16949 환경/)).toBeInTheDocument();
    // 복수선택은 고른 보기 모두.
    expect(screen.getByText(/A\. 10건 미만, B\. 10~30건/)).toBeInTheDocument();
    // 종전의 "1: A" 나열로 떨어지지 않는다.
    expect(screen.queryByText(/1: A · 2: A,B/)).toBeNull();
  });

  it("answers가 없으면 백엔드의 text를 그대로 쓴다", () => {
    // 자유 서술 답변, 또는 answers를 안 실어 보내는 구 백엔드.
    render(
      <LocaleProvider locale="en">
        <Harness items={[{ ...answerItem, answers: null,
                           text: "답변 제출: 자유 서술" }]} />
      </LocaleProvider>,
    );
    expect(screen.getByText("답변 제출: 자유 서술")).toBeInTheDocument();
  });
});
