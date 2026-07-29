import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PrototypeCard } from "./PrototypeCard";
import type { PrototypeInfo } from "@/lib/api/prototypes";

function info(overrides: Partial<PrototypeInfo>): PrototypeInfo {
  return {
    slug: "todo-app",
    spec_path: "aiplc-docs/discovery/prototypes/todo-app/PROTOTYPE-todo-app.md",
    state: "none",
    port: null,
    response_count: 0,
    ...overrides,
  };
}

const noop = {
  onBuild: vi.fn(),
  onStartHost: vi.fn(),
  onStopHost: vi.fn(),
};

describe("PrototypeCard", () => {
  it("none: shows the spec-only badge and a single 빌드 시작 button", () => {
    render(<PrototypeCard info={info({ state: "none" })} busy={false} {...noop} />);
    expect(screen.getByText("스펙만 있음")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "호스팅 시작" })).not.toBeInTheDocument();
  });

  it("none: clicking 빌드 시작 calls onBuild", async () => {
    const user = userEvent.setup();
    const onBuild = vi.fn();
    render(<PrototypeCard info={info({ state: "none" })} busy={false} {...noop} onBuild={onBuild} />);
    await user.click(screen.getByRole("button", { name: "빌드 시작" }));
    expect(onBuild).toHaveBeenCalledTimes(1);
  });

  it("building: shows a pulsing 빌드 중 badge and 세션 열기 button", () => {
    render(<PrototypeCard info={info({ state: "building" })} busy={false} {...noop} />);
    const badge = screen.getByText("빌드 중");
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain("animate-pulse");
    expect(screen.getByRole("button", { name: "세션 열기" })).toBeInTheDocument();
  });

  it("built: shows 빌드 완료 badge plus 호스팅 시작 and 다시 빌드 buttons", async () => {
    const user = userEvent.setup();
    const onStartHost = vi.fn();
    const onBuild = vi.fn();
    render(
      <PrototypeCard info={info({ state: "built" })} busy={false} {...noop} onStartHost={onStartHost} onBuild={onBuild} />,
    );
    expect(screen.getByText("빌드 완료")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "호스팅 시작" }));
    expect(onStartHost).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "다시 빌드" }));
    expect(onBuild).toHaveBeenCalledTimes(1);
  });

  it("running: shows the port in the badge, a preview link, 호스팅 중지, and 로그", async () => {
    const user = userEvent.setup();
    const onOpenPreview = vi.fn();
    const onStopHost = vi.fn();
    const onShowLogs = vi.fn();
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4021 })}
        busy={false}
        {...noop}
        onOpenPreview={onOpenPreview}
        onStopHost={onStopHost}
        onShowLogs={onShowLogs}
      />,
    );
    expect(screen.getByText("실행 중 :4021")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "프리뷰 열기" }));
    expect(onOpenPreview).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "호스팅 중지" }));
    expect(onStopHost).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "로그" }));
    expect(onShowLogs).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "빌드 시작" })).not.toBeInTheDocument();
  });

  it("running: omits the preview/logs buttons when their handlers aren't passed", () => {
    render(<PrototypeCard info={info({ state: "running", port: 4021 })} busy={false} {...noop} />);
    expect(screen.queryByRole("button", { name: "프리뷰 열기" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "로그" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "호스팅 중지" })).toBeInTheDocument();
  });

  it("failed: shows a rose 실패 badge plus 다시 빌드 and 로그", () => {
    render(<PrototypeCard info={info({ state: "failed" })} busy={false} {...noop} onShowLogs={vi.fn()} />);
    const badge = screen.getByText("실패");
    expect(badge.className).toContain("rose");
    expect(screen.getByRole("button", { name: "다시 빌드" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "호스팅 시작" })).not.toBeInTheDocument();
  });

  it("offers a download link once a bundle exists", () => {
    render(
      <PrototypeCard
        info={info({ state: "built" })}
        onBuild={() => {}}
        onStartHost={() => {}}
        onStopHost={() => {}}
        archiveUrl="/api/projects/p1/prototypes/demo/archive"
        busy={false}
      />,
    );
    const link = screen.getByRole("link", { name: "다운로드" });
    expect(link).toHaveAttribute("href", "/api/projects/p1/prototypes/demo/archive");
  });

  it("offers download while running too", () => {
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4001 })}
        onBuild={() => {}}
        onStartHost={() => {}}
        onStopHost={() => {}}
        archiveUrl="/api/x"
        busy={false}
      />,
    );
    expect(screen.getByRole("link", { name: "다운로드" })).toBeInTheDocument();
  });

  it("hides download when there is nothing built yet", () => {
    render(
      <PrototypeCard
        info={info({ state: "none" })}
        onBuild={() => {}}
        onStartHost={() => {}}
        onStopHost={() => {}}
        archiveUrl="/api/x"
        busy={false}
      />,
    );
    expect(screen.queryByRole("link", { name: "다운로드" })).toBeNull();
  });

  it("busy disables every visible action button", () => {
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4021 })}
        busy={true}
        {...noop}
        onOpenPreview={vi.fn()}
        onShowLogs={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "프리뷰 열기" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "호스팅 중지" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "로그" })).toBeDisabled();
  });
});
