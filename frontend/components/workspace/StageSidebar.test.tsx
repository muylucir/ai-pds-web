import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageSidebar, mergeStages } from "./StageSidebar";
import { LocaleProvider } from "@/lib/i18n/provider";
import { projectState } from "@/test/fixtures/projectState";
import type { StageState } from "@/lib/api/types";

describe("mergeStages", () => {
  it("returns the server stages unchanged when there are no events", () => {
    const server: StageState[] = [{ name: "Envision", status: "pending", note: null }];
    expect(mergeStages(server, [])).toEqual(server);
  });

  it("overrides an existing stage's status/note (summary→note) by stage-name match", () => {
    const server: StageState[] = [{ name: "Envision", status: "pending", note: "old note" }];
    const merged = mergeStages(server, [{ stage: "Envision", status: "in_progress", summary: "새 진행 상황" }]);
    expect(merged).toEqual([{ name: "Envision", status: "in_progress", note: "새 진행 상황" }]);
  });

  it("keeps the previous note when an event's summary is empty", () => {
    const server: StageState[] = [{ name: "Envision", status: "pending", note: "old note" }];
    const merged = mergeStages(server, [{ stage: "Envision", status: "in_progress", summary: "" }]);
    expect(merged[0].note).toBe("old note");
  });

  it("appends a stage the server didn't know about yet, defaulting from 'pending'", () => {
    const merged = mergeStages([], [{ stage: "New Stage", status: "in_progress", summary: "시작" }]);
    expect(merged).toEqual([{ name: "New Stage", status: "in_progress", note: "시작" }]);
  });

  it("the latest event for a stage wins when multiple events target the same stage", () => {
    const merged = mergeStages(
      [{ name: "Envision", status: "pending", note: null }],
      [
        { stage: "Envision", status: "in_progress", summary: "1차" },
        { stage: "Envision", status: "completed", summary: "2차" },
      ],
    );
    expect(merged).toEqual([{ name: "Envision", status: "completed", note: "2차" }]);
  });
});

describe("StageSidebar", () => {
  it("renders every merged stage name (server state, no events yet)", () => {
    render(<StageSidebar state={projectState} events={[]} />);
    expect(screen.getByLabelText("스테이지 진행 상황")).toBeInTheDocument();
    for (const s of projectState.stages) {
      expect(screen.getByText(s.name)).toBeInTheDocument();
    }
  });

  it("reflects a stage event's status override over the server's initial state", () => {
    render(
      <StageSidebar
        state={projectState}
        events={[{ stage: "Go-to-Market", status: "in_progress", summary: "마케팅 전략 초안" }]}
      />,
    );
    expect(screen.getByText("마케팅 전략 초안")).toBeInTheDocument();
  });

  // 이 카운터는 딕셔너리를 거치지 않고 "스테이지"를 리터럴로 박고 있었다 —
  // 영어 UI에서 헤딩과 힌트는 영어인데 그 줄만 한국어로 남았다(2026-08-04의
  // 스크린샷에 "0 / 0 스테이지"로 찍혀 있다). CanvasSidebar는 같은 자리에서
  // 이미 t("canvas.stageUnit")을 쓰므로, 두 사이드바가 어긋나 있었다.
  it("renders the stage-count unit in the UI locale, not a hardcoded Korean literal", () => {
    render(
      <LocaleProvider locale="en">
        <StageSidebar state={projectState} events={[]} />
      </LocaleProvider>,
    );
    const { completed, total } = {
      completed: projectState.stages.filter((s) => s.status === "completed").length,
      total: projectState.stages.length,
    };
    expect(screen.getByText(new RegExp(`${completed} / ${total} stages`))).toBeInTheDocument();
    expect(screen.queryByText(/스테이지/)).toBeNull();
  });
});
