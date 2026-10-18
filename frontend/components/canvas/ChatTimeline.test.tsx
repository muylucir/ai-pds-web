// frontend/components/canvas/ChatTimeline.test.tsx  (full replacement)
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatTimeline } from "./ChatTimeline";
import type { ChatItem } from "@/lib/useWorkspaceStream";
import { LocaleProvider } from "@/lib/i18n/provider";

// Shared render helper for the stick-to-bottom suite below: a thin wrapper
// over ChatTimeline with sensible defaults so each test only varies `items`
// and `stickSignal`.
function Harness({ items, stickSignal }: { items: ChatItem[]; stickSignal?: number }) {
  return (
    <ChatTimeline
      items={items}
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
      <ChatTimeline items={items} />,
    );
    expect(screen.getByText("필터 추가해줘")).toBeInTheDocument();
    expect(screen.getByText("추가했습니다.")).toBeInTheDocument();
  });

  it("renders an empty state with no items", () => {
    render(<ChatTimeline items={[]} />);
    expect(screen.getByText(/대화를 시작해 보세요/)).toBeInTheDocument();
  });

  it("히스토리 로딩 중에는 빈 상태 문구를 숨긴다 (부모가 그리는 스켈레톤과 겹치지 않도록)", () => {
    render(
      <ChatTimeline
        items={[]}
        historyLoading
      />,
    );
    expect(screen.queryByText(/대화를 시작해 보세요/)).not.toBeInTheDocument();
  });

  it("renders the verbatim typing-hint chrome once there is at least one item, but not on the empty state", () => {
    const items: ChatItem[] = [{ id: "u1", role: "user", text: "필터 추가해줘" }];
    const { rerender } = render(
      <ChatTimeline items={items} />,
    );
    expect(screen.getByText(/버튼 대신 채팅으로 답해도 됩니다/)).toBeInTheDocument();
    rerender(<ChatTimeline items={[]} />);
    expect(screen.queryByText(/버튼 대신 채팅으로 답해도 됩니다/)).not.toBeInTheDocument();
  });

  it("renders a history-card item (from useWorkspaceStream) as a static summary, naming the questions file", () => {
    const items: ChatItem[] = [{ id: "h1", role: "history-card", name: "mode-selection" }];
    render(
      <ChatTimeline items={items} />,
    );
    expect(screen.getByText(/질문지 제시됨/)).toBeInTheDocument();
    expect(screen.getByText(/mode-selection/)).toBeInTheDocument();
  });

  it("renders a history-card item with no name without the trailing dash", () => {
    const items: ChatItem[] = [{ id: "h2", role: "history-card", name: null }];
    render(
      <ChatTimeline items={items} />,
    );
    expect(screen.getByText("📋 질문지 제시됨")).toBeInTheDocument();
  });

  it("복원된 질문 payload가 있으면 문항 수를 보여주고 펼쳐서 질문·보기를 읽는다", async () => {
    // Previously it was just the single line "질문지 제시됨" (questionnaire
    // presented), so the scrollback gave no way to tell what had been asked —
    // the payload remained structured in the transcript's tool_use.input, but
    // the restore code was discarding it.
    const items: ChatItem[] = [{
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
      <ChatTimeline items={items} />,
    );
    expect(screen.getByText(/1문항/)).toBeInTheDocument();
    // When collapsed, the questions are not visible (so the card does not
    // swallow the timeline).
    expect(screen.queryByText(/무엇을 만드나요\?/)).toBeNull();

    await userEvent.setup().click(screen.getByRole("button", { name: "펼치기" }));

    expect(screen.getByText(/Q1\. 무엇을 만드나요\?/)).toBeInTheDocument();
    expect(screen.getByText("A. 자동차 부품")).toBeInTheDocument();
    // is_other is a free-input placeholder — it is not a presented option, so
    // it is not listed.
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
    // User scrolls up: put scrollTop far from the bottom and fire a scroll
    // event
    el.scrollTop = 100;
    fireEvent.scroll(el);
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={0} />);
    expect(el.scrollTop).toBe(100); // position preserved — not dragged down
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
  // answers arrive on the UserItem (useWorkspaceStream.historyItemToChatItem
  // copies HistoryItem.answers from GET /history as is).
  const answerItem: ChatItem = {
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
    // Ignores the Korean text the backend sent and rebuilds it in the UI
    // language.
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

  // This combination (answers + questions) comes from sessions that have an
  // answer record. Previously the restored bubble was the single line
  // `답변 제출: Your questions have been answered: "질문"="라벨"` — the backend
  // could not unpack the English sentence the CLI wrote, so it sent it as is, and the
  // question numbers, option letters, and option texts were all lost.
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
    // The question numbers and original question text are visible.
    expect(screen.getByText(/Q1\. 무엇을 만드나요\?/)).toBeInTheDocument();
    // Expanded to the option text, not just the letter.
    expect(screen.getByText(/A\. 자동차 부품 — IATF 16949 환경/)).toBeInTheDocument();
    // Multi-select shows every chosen option.
    expect(screen.getByText(/A\. 10건 미만, B\. 10~30건/)).toBeInTheDocument();
    // Does not fall back to the old "1: A" listing.
    expect(screen.queryByText(/1: A · 2: A,B/)).toBeNull();
  });

  it("answers가 없으면 백엔드의 text를 그대로 쓴다", () => {
    // Free-text answers, or an older backend that does not send answers.
    render(
      <LocaleProvider locale="en">
        <Harness items={[{ ...answerItem, answers: null,
                           text: "답변 제출: 자유 서술" }]} />
      </LocaleProvider>,
    );
    expect(screen.getByText("답변 제출: 자유 서술")).toBeInTheDocument();
  });
});
