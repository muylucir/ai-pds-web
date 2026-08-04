// frontend/components/canvas/ActivityIndicator.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { ActivityIndicator, activityLabel, formatElapsed } from "./ActivityIndicator";
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

describe("ActivityIndicator — 살아있음의 증거", () => {
  it("경과 시간이 1초마다 올라간다", () => {
    vi.useFakeTimers();
    render(<ActivityIndicator tool="Read" />);

    expect(screen.getByText("0초")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(3000));
    expect(screen.getByText("3초")).toBeInTheDocument();
  });

  it("탭이 백그라운드로 갔다 와도 실제 경과를 반영한다", () => {
    // setInterval 호출 횟수를 세면 브라우저 throttle에 속아 실제보다 적게
    // 센다. Date.now() 차이로 계산하므로 타이머가 한 번만 깨어나도 옳다.
    vi.useFakeTimers();
    render(<ActivityIndicator tool="Read" />);
    act(() => void vi.advanceTimersByTime(30_000));
    expect(screen.getByText("30초")).toBeInTheDocument();
  });

  it("회전 애니메이션을 쓴다 — 투명도만 변하는 맥동은 정지 화면과 구분되지 않는다", () => {
    const { container } = render(<ActivityIndicator tool="Read" />);
    expect(container.querySelector(".animate-spin")).not.toBeNull();
  });

  it("role=status로 알리고, 경과 시간은 스크린리더에서 제외한다", () => {
    vi.useFakeTimers();
    render(<ActivityIndicator tool="Write" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("문서를 작성하고 있어요");
    // 1초마다 읽어주면 라벨 변화를 덮어버린다.
    expect(screen.getByText("0초")).toHaveAttribute("aria-hidden", "true");
  });
});
