// frontend/components/canvas/LiveActivityBar.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { LiveActivityBar, activityLabel, formatElapsed } from "./LiveActivityBar";
import type { LiveActivity } from "@/lib/useTurnStream";

const tool = (t: string, detail: string | null = null): LiveActivity =>
  ({ kind: "tool", tool: t, detail });
import { dictFor, type Dict } from "@/lib/i18n";

// 실제 딕셔너리를 탄다 — 가짜 t를 넘기면 이 문구들이 딕셔너리에서 온다는 사실이
// 테스트에서 사라진다. 기본 로케일(ko)이므로 단정은 한국어 그대로다.
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
    // setInterval 호출 횟수를 세면 브라우저 throttle에 속아 실제보다 적게
    // 센다. Date.now() 차이로 계산하므로 타이머가 한 번만 깨어나도 옳다.
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
    // 1초마다 읽어주면 라벨 변화를 덮어버린다.
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

  it("한 줄을 넘기지 않는다 — 긴 경로가 입력창을 밀어내면 안 된다", () => {
    const { container } = render(
      <LiveActivityBar activity={tool("Read", "a/".repeat(80) + "deep.md")} />);
    const line = container.querySelector("[data-testid='live-what']");
    expect(line).toHaveClass("truncate");
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

describe("Claude Agent SDK 도구명 라벨 (regression)", () => {
  // 드라이버가 바뀌면 status 이벤트의 도구 이름이 SDK 내장 이름으로 온다.
  // 매핑에 없으면 폴백이 발동해 사용자에게 "Write 실행 중…" 같은 영어 도구명이
  // 노출된다 — 크래시는 아니지만 UX가 조용히 나빠진다.
  const CASES: Array<[string, RegExp]> = [
    ["AskUserQuestion", /질문을 준비하고 있어요/],
    ["Write", /문서를 작성하고 있어요/],
    ["Edit", /문서를 작성하고 있어요/],
    ["MultiEdit", /문서를 작성하고 있어요/],
    ["Read", /자료를 확인하고 있어요/],
    ["Glob", /자료를 찾고 있어요/],
    // CLI 기본 도구 (tools=None이므로 사용 가능; envision.md의 URL 분석 모드 B/C와 workspace 탐색 필요)
    ["Grep", /자료를 찾고 있어요/],
    ["WebFetch", /정보를 수집하고 있어요/],
    ["Bash", /작업을 진행하고 있어요/],
  ];

  for (const [name, label] of CASES) {
    it(`maps ${name} to a Korean activity label`, () => {
      render(<LiveActivityBar activity={tool(name)} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      // 영어 도구명이 그대로 보이면 안 된다.
      expect(screen.queryByText(new RegExp(`${name} 실행 중`))).toBeNull();
    });
  }

  it("keeps the Strands tool names working during the env-toggle period", () => {
    // 두 드라이버가 공존하는 기간에는 양쪽 다 올바른 라벨이 나와야 한다.
    render(<LiveActivityBar activity={tool("file_write")} />);
    expect(screen.getByText(/문서를 작성하고 있어요/)).toBeInTheDocument();
  });
});
