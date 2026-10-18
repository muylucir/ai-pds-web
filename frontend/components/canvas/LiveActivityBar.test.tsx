// frontend/components/canvas/LiveActivityBar.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { LiveActivityBar, activityLabel, activityText, formatElapsed } from "./LiveActivityBar";
import type { AgentRow } from "@/lib/protoAgents";
import type { LiveActivity } from "@/lib/chatItems";

const tool = (t: string, detail: string | null = null): LiveActivity =>
  ({ kind: "tool", tool: t, detail });
import { dictFor, type Dict } from "@/lib/i18n";

// Uses the real dictionary — passing a fake t would lose from the test the fact that these strings come
// from the dictionary. It is the default locale (ko), so the assertions are in Korean as is.
const koDict = dictFor("ko");
const t = (key: keyof Dict) => koDict[key];

afterEach(() => vi.useRealTimers());

describe("formatElapsed", () => {
  it("60초 미만은 초만 보여준다", () => {
    expect(formatElapsed(0, t)).toBe("0초");
    expect(formatElapsed(59, t)).toBe("59초");
  });

  it("60초 이상은 분을 함께 보여준다 — 세 자리 초는 크기가 한눈에 안 읽힌다", () => {
    expect(formatElapsed(60, t)).toBe("1분");
    expect(formatElapsed(95, t)).toBe("1분 35초");
    expect(formatElapsed(600, t)).toBe("10분");
  });
});

describe("activityLabel", () => {
  it("도구가 없으면 생각 중으로 대체한다 — 턴 시작 직후 구간이 가장 불안하다", () => {
    expect(activityLabel(null, t)).toBe("생각하고 있어요");
    expect(activityLabel(undefined, t)).toBe("생각하고 있어요");
  });

  it("build_complete도 라벨이 있다 — 폴백이 영어 도구명을 노출하면 안 된다", () => {
    expect(activityLabel("build_complete", t)).toBe("빌드를 마무리하고 있어요");
  });

  it("모르는 도구는 폴백으로 도구명을 그대로 쓴다", () => {
    expect(activityLabel("weird_tool", t)).toBe("weird_tool 실행 중");
  });
});

describe("LiveActivityBar — 살아있음의 증거", () => {
  it("경과 시간이 1초마다 올라간다", () => {
    vi.useFakeTimers();
    render(<LiveActivityBar activity={tool("Read")} />);

    expect(screen.getByText("0초")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(3000));
    expect(screen.getByText("3초")).toBeInTheDocument();
  });

  it("탭이 백그라운드로 갔다 와도 실제 경과를 반영한다", () => {
    // Counting setInterval calls is fooled by browser throttling and undercounts the real
    // time. It computes from the Date.now() difference, so it is correct even if the timer wakes only once.
    vi.useFakeTimers();
    render(<LiveActivityBar activity={tool("Read")} />);
    act(() => void vi.advanceTimersByTime(30_000));
    expect(screen.getByText("30초")).toBeInTheDocument();
  });

  it("회전 애니메이션을 쓴다 — 투명도만 변하는 맥동은 정지 화면과 구분되지 않는다", () => {
    const { container } = render(<LiveActivityBar activity={tool("Read")} />);
    expect(container.querySelector(".animate-spin")).not.toBeNull();
  });

  it("role=status로 알리고, 경과 시간은 스크린리더에서 제외한다", () => {
    vi.useFakeTimers();
    render(<LiveActivityBar activity={tool("Write")} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("문서를 작성하고 있어요");
    // Reading it out every second would drown out label changes.
    expect(screen.getByText("0초")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("LiveActivityBar — 무엇을 보여주는가", () => {
  it("사고 중이면 사고를 말한다", () => {
    render(<LiveActivityBar activity={{ kind: "thinking" }} />);
    expect(screen.getByText("생각하고 있어요")).toBeInTheDocument();
  });

  it("도구면 도구 라벨을 보여준다", () => {
    render(<LiveActivityBar activity={tool("Read")} />);
    expect(screen.getByText("자료를 확인하고 있어요")).toBeInTheDocument();
  });
});

describe("LiveActivityBar — 종류별 문구", () => {
  it("작성 중이면 작성을 말한다 — 도구가 끝나고 답변이 흐르는 구간이다", () => {
    render(<LiveActivityBar activity={{ kind: "writing" }} />);
    expect(screen.getByText("문서를 작성하고 있어요")).toBeInTheDocument();
  });

  it("도구의 대상(파일·명령)을 함께 보여준다 — 무엇을 하는지가 요점이다", () => {
    render(<LiveActivityBar activity={tool("Read", "aiplc-docs/x.md")} />);
    expect(screen.getByText(/aiplc-docs\/x\.md/)).toBeInTheDocument();
  });

  it("파일 변경은 라벨과 경로를 보여준다", () => {
    render(<LiveActivityBar activity={{ kind: "file", path: "aiplc-docs/audit.md" }} />);
    expect(screen.getByText(/파일 변경/)).toBeInTheDocument();
    expect(screen.getByText(/aiplc-docs\/audit\.md/)).toBeInTheDocument();
  });

  it("각 줄이 넘치지 않는다 — 긴 경로가 입력창을 밀어내면 안 된다", () => {
    const long = "a/".repeat(80) + "deep.md";
    render(<LiveActivityBar activity={tool("Read", long)} />);
    expect(screen.getByTestId("live-what")).toHaveClass("truncate");
    const target = screen.getByTestId("live-what-target");
    expect(target).toHaveClass("truncate");
    expect(target).toHaveAttribute("title", long);   // the truncated part is seen by hovering
  });

  // While the target was attached after the label it was truncated and barely readable — it moves to a second line.
  it("대상(명령·파일)은 라벨과 다른 둘째 줄에 있다", () => {
    render(<LiveActivityBar activity={tool("Read", "aiplc-docs/x.md")} />);
    expect(screen.getByTestId("live-what")).not.toHaveTextContent("aiplc-docs/x.md");
    expect(screen.getByTestId("live-what-target")).toHaveTextContent("aiplc-docs/x.md");
  });

  // When thinking → reading a file, a bar flipping between 1 and 2 lines makes the input box jump.
  it("대상이 없어도 둘째 줄 자리를 비워 둔다", () => {
    render(<LiveActivityBar activity={{ kind: "thinking" }} />);
    const target = screen.getByTestId("live-what-target");
    expect(target.textContent).toBe("\u00a0");
    expect(target).toHaveClass("min-h-4");
  });
});

describe("LiveActivityBar — 살아있음의 기본값", () => {
  it("도구가 아직 하나도 안 돌았어도 표시가 있다 — 그 구간이 가장 불안하다", () => {
    render(<LiveActivityBar activity={{ kind: "thinking" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("생각하고 있어요");
  });

  it("경과 시간을 함께 보여준다 — 3초짜리와 40초짜리를 구분할 근거", () => {
    render(<LiveActivityBar activity={{ kind: "thinking" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("0초");
  });

  it("알 수 없는 도구명은 폴백 문구로 표시한다", () => {
    render(<LiveActivityBar activity={tool("custom_tool")} />);
    expect(screen.getByText(/custom_tool 실행 중/)).toBeInTheDocument();
  });
});

describe("LiveActivityBar — 서브에이전트 행", () => {
  // What this block guards: parallel work reads as progress on screen. A one-line bar
  // flickered between three agents and none of them read as progress.
  const row = (over: Partial<AgentRow> = {}): AgentRow => ({
    id: "a040",
    label: "화면 골격",
    tool: "Write",
    detail: "app/page.tsx",
    startedAt: Date.now(),
    status: null,
    summary: null,
    ...over,
  });

  // Subagent rows are all 2 lines (assigned task / file being touched); the overall row only states the count, so 1 line.
  it("에이전트 행은 대상을 둘째 줄에 두고, 총괄 행은 둘째 줄을 두지 않는다", () => {
    const { container } = render(
      <LiveActivityBar
        activity={tool("Agent")}
        agents={[row({ id: "a1", label: "화면 골격", detail: "app/page.tsx" })]}
      />,
    );
    expect(screen.queryByTestId("live-what-target")).toBeNull();
    const target = screen.getByText("app/page.tsx");
    expect(target.tagName).toBe("P");
    expect(target).toHaveClass("truncate");
    expect(screen.getByText("화면 골격")).not.toHaveTextContent("app/page.tsx");
    // The bar itself grows only within its height cap — it does not eat up the whole conversation area.
    expect(container.querySelector("[role='status']")).toHaveClass("max-h-[40vh]", "overflow-y-auto");
  });

  it("에이전트마다 한 행씩 그린다", () => {
    render(
      <LiveActivityBar
        activity={tool("Agent")}
        agents={[
          row({ id: "a1", label: "화면 골격", detail: "app/page.tsx" }),
          row({ id: "a2", label: "데이터 모델", detail: "prisma/schema.prisma" }),
          row({ id: "a3", label: "스타일", detail: "tailwind.config.ts" }),
        ]}
      />,
    );
    expect(screen.getByText("화면 골격")).toBeInTheDocument();
    expect(screen.getByText("데이터 모델")).toBeInTheDocument();
    expect(screen.getByText("스타일")).toBeInTheDocument();
    expect(screen.getByText(/app\/page\.tsx/)).toBeInTheDocument();
  });

  it("총괄 줄이 몇 개가 일하는지 말한다", () => {
    // During that stretch all the overall agent does is wait. What the user wants to know is
    // the count, and previously the subagents' tool names flickered in that spot.
    render(<LiveActivityBar activity={tool("Agent")} agents={[row({ id: "a1" }), row({ id: "a2" })]} />);
    expect(screen.getByTestId("live-what")).toHaveTextContent("2개 에이전트가 일하고 있어요");
  });

  it("행마다 자기 경과 시간을 센다", () => {
    // Counting from mount would make a late-starting agent look as if it had already worked a long time.
    vi.useFakeTimers();
    const now = Date.now();
    render(
      <LiveActivityBar
        activity={tool("Agent")}
        agents={[row({ id: "a1", startedAt: now - 5_000 }), row({ id: "a2", startedAt: now - 40_000 })]}
      />,
    );
    // The overall line counts from mount (0초), and the two rows each from when they started.
    expect(screen.getByText("0초")).toBeInTheDocument();
    expect(screen.getByText("5초")).toBeInTheDocument();
    expect(screen.getByText("40초")).toBeInTheDocument();
  });

  it("행마다 스피너가 돈다 — 정지 화면과 구분되는 유일한 신호다", () => {
    const { container } = render(
      <LiveActivityBar activity={tool("Agent")} agents={[row({ id: "a1" }), row({ id: "a2" })]} />);
    // 1 overall line + 2 rows.
    expect(container.querySelectorAll(".animate-spin")).toHaveLength(3);
  });

  it("대상이 없으면 무슨 도구를 돌리는지라도 말한다", () => {
    render(<LiveActivityBar activity={tool("Agent")} agents={[row({ detail: null, tool: "Bash" })]} />);
    expect(screen.getByText(/작업을 진행하고 있어요/)).toBeInTheDocument();
  });

  it("라벨을 못 받은 행도 그린다 — 도는 것이 안 보이는 것보다 낫다", () => {
    render(<LiveActivityBar activity={tool("Agent")} agents={[row({ label: null })]} />);
    expect(screen.getByText("에이전트")).toBeInTheDocument();
  });

  it("에이전트가 없으면 종전과 똑같은 한 줄이다", () => {
    // The behavior of non-parallel builds (and the workspace screen) must not change.
    const { container } = render(<LiveActivityBar activity={tool("Read", "x.md")} />);
    expect(container.querySelectorAll(".animate-spin")).toHaveLength(1);
    expect(screen.getByTestId("live-what")).toHaveTextContent("자료를 확인하고 있어요");
  });

  it("긴 대상이 행을 넘치게 하지 않는다", () => {
    const { container } = render(
      <LiveActivityBar
        activity={tool("Agent")}
        agents={[row({ detail: "a/".repeat(80) + "deep.tsx" })]}
      />,
    );
    for (const line of container.querySelectorAll("span.flex-1")) {
      expect(line).toHaveClass("truncate");
    }
  });
});

describe("Claude Agent SDK 도구명 라벨 (regression)", () => {
  // When the driver changes, tool names in status events arrive as the SDK's built-in names.
  // If missing from the mapping, the fallback kicks in and exposes English tool names to the user such as "Write 실행 중…"
  // — not a crash, but the UX silently degrades.
  const CASES: Array<[string, RegExp]> = [
    ["AskUserQuestion", /질문을 준비하고 있어요/],
    ["Write", /문서를 작성하고 있어요/],
    ["Edit", /문서를 작성하고 있어요/],
    ["MultiEdit", /문서를 작성하고 있어요/],
    ["Read", /자료를 확인하고 있어요/],
    ["Glob", /자료를 찾고 있어요/],
    // CLI default tools (available since tools=None; needed for envision.md's URL analysis modes B/C and workspace exploration)
    ["Grep", /자료를 찾고 있어요/],
    ["WebFetch", /정보를 수집하고 있어요/],
    ["Bash", /작업을 진행하고 있어요/],
  ];

  for (const [name, label] of CASES) {
    it(`maps ${name} to a Korean activity label`, () => {
      render(<LiveActivityBar activity={tool(name)} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      // English tool names must not show as is.
      expect(screen.queryByText(new RegExp(`${name} 실행 중`))).toBeNull();
    });
  }

  it("keeps the Strands tool names working during the env-toggle period", () => {
    // During the period when both drivers coexist, both must produce the correct labels.
    render(<LiveActivityBar activity={tool("file_write")} />);
    expect(screen.getByText(/문서를 작성하고 있어요/)).toBeInTheDocument();
  });
});

describe("activityText — 쓰고 있는 파일", () => {
  const drafting = (path: string | null, chars: number): LiveActivity =>
    ({ kind: "drafting", path, chars });

  it("문서·질문지·기록을 가르고, 파일 이름과 글자 수를 대상으로 단다", () => {
    expect(activityText(drafting("aiplc-docs/discovery/discovery-document.md", 1234), t))
      .toEqual({ label: "문서 작성 중", target: "discovery-document.md · 1,234자" });
    expect(activityText(drafting("aiplc-docs/discovery/envision/pain-point-questions.md", 50), t).label)
      .toBe("질문지 작성 중");
    expect(activityText(drafting("aiplc-docs/audit.md", 10), t).label).toBe("기록 중");
    expect(activityText(drafting("aiplc-docs/aiplc-state.md", 10), t).label).toBe("기록 중");
  });

  it("경로가 확정되기 전에는 쓰기 시작만 알린다", () => {
    expect(activityText(drafting(null, 0), t)).toEqual({ label: t("activity.writing"), target: null });
  });
});
