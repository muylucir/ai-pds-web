import { afterEach, describe, it, expect, vi } from "vitest";
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
    access_url: null,
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
    expect(screen.getByText("빌드 전")).toBeInTheDocument();
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

  // 링크 복사. 호스팅 중일 때만 노출하는 이유는 그때만 링크가 동작하기
  // 때문이다 — built 상태의 링크는 백엔드가 502를 준다
  // (routes/proto_public.py). 깨진 링크를 공유하게 만들지 않는다.
  describe("링크 복사", () => {
    // userEvent.setup()이 navigator.clipboard를 getter로 심어 두므로
    // Object.assign은 "has only a getter"로 던진다. defineProperty로 덮고
    // afterEach에서 원래 서술자를 되돌린다 — 그러지 않으면 이 스텁이 다음
    // 테스트의 userEvent까지 오염시킨다.
    const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    afterEach(() => {
      if (original) Object.defineProperty(navigator, "clipboard", original);
      else Reflect.deleteProperty(navigator as unknown as object, "clipboard");
    });

    function clipboardSpy(impl?: () => Promise<void>) {
      const writeText = vi.fn(impl ?? (() => Promise.resolve()));
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText }, configurable: true, writable: true,
      });
      return writeText;
    }

    it("running: copies an absolute, shareable URL", async () => {
      const user = userEvent.setup();
      const writeText = clipboardSpy();
      render(
        <PrototypeCard
          info={info({ state: "running", port: 4021 })}
          busy={false}
          {...noop}
          shareUrl="https://d123.cloudfront.net/api/proto/p1/todo-app/"
        />,
      );

      await user.click(screen.getByRole("button", { name: "링크 복사" }));

      expect(writeText).toHaveBeenCalledWith("https://d123.cloudfront.net/api/proto/p1/todo-app/");
    });

    // 가짜 타이머를 쓰지 않는다: userEvent가 자체적으로 타이머를 쓰기 때문에
    // 이 조합이 클릭 대기에서 매달리고, 남은 실제 타이머가 다음 테스트까지
    // 끌어간다(실측: 이 테스트와 뒤의 두 개가 5초 타임아웃). 되돌아온다는
    // 사실만 확인하면 되므로 findBy*의 폴링에 2초를 맡긴다.
    it("confirms the copy, then goes back so a second copy is visible", async () => {
      const user = userEvent.setup();
      clipboardSpy();
      render(
        <PrototypeCard
          info={info({ state: "running", port: 4021 })}
          busy={false}
          {...noop}
          shareUrl="https://x/api/proto/p1/todo-app/"
        />,
      );

      await user.click(screen.getByRole("button", { name: "링크 복사" }));
      expect(await screen.findByRole("button", { name: "복사됨" })).toBeInTheDocument();

      // 2초 뒤 라벨이 돌아온다 — 그래야 두 번째 복사가 화면에서 구별된다.
      expect(await screen.findByRole("button", { name: "링크 복사" }, { timeout: 3000 }))
        .toBeInTheDocument();
    });

    it("does not claim success when the clipboard is unavailable", async () => {
      const user = userEvent.setup();
      // 비-HTTPS 오리진이나 권한 거부 — 조용히 성공한 척하면 사용자가 빈
      // 클립보드를 붙여넣게 된다.
      clipboardSpy(() => Promise.reject(new Error("denied")));
      render(
        <PrototypeCard
          info={info({ state: "running", port: 4021 })}
          busy={false}
          {...noop}
          shareUrl="https://x/api/proto/p1/todo-app/"
        />,
      );

      await user.click(screen.getByRole("button", { name: "링크 복사" }));

      expect(screen.getByRole("button", { name: "링크 복사" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "복사됨" })).toBeNull();
    });

    it("is absent before hosting starts — that link would 502", () => {
      render(
        <PrototypeCard
          info={info({ state: "built" })}
          busy={false}
          {...noop}
          shareUrl="https://x/api/proto/p1/todo-app/"
        />,
      );
      expect(screen.queryByRole("button", { name: "링크 복사" })).toBeNull();
    });

    it("is absent when no shareUrl is supplied", () => {
      render(<PrototypeCard info={info({ state: "running", port: 4021 })} busy={false} {...noop} />);
      expect(screen.queryByRole("button", { name: "링크 복사" })).toBeNull();
    });
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
        onReset={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "프리뷰 열기" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "호스팅 중지" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "로그" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /초기화/ })).toBeDisabled();
  });

  it("offers reset once a prototype has been built", async () => {
    const user = userEvent.setup();
    const onReset = vi.fn();
    render(<PrototypeCard info={info({ state: "built" })} busy={false} {...noop} onReset={onReset} />);

    await user.click(screen.getByRole("button", { name: /초기화/ }));

    expect(onReset).toHaveBeenCalledWith("todo-app");
  });

  it("does not offer reset for a prototype with nothing to reset", () => {
    render(<PrototypeCard info={info({ state: "none" })} busy={false} {...noop} onReset={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /초기화/ })).toBeNull();
  });
});

// ---- 호스팅 시작 중의 진행 표시 (2026-08-19) ----
// `POST /host`가 npm install → npm run build → 포트 대기(최대 60초)를 전부
// await한 뒤 응답한다. 그동안 카드는 "빌드 완료 + 비활성 버튼"으로 멈춰 있었고,
// 실측 13분짜리 구간이라 사용자가 "아무 반응 없음"으로 읽었다. 서버는 그 단계를
// 이미 기록하므로(ProtoHost._registry의 state) 보여주기만 하면 된다.

const BUILT_Q: PrototypeInfo = {
  slug: "prototype",
  spec_path: "aiplc-docs/discovery/prototype/prototype-spec.md",
  state: "built",
  port: null,
  access_url: null,
  response_count: 0,
};

describe("호스팅 시작 중 진행 표시", () => {
  it("단계를 배지에 보여준다 — 목록의 '빌드 완료'보다 정확하다", () => {
    render(<PrototypeCard info={BUILT_Q} busy startingPhase="installing"
                          onBuild={vi.fn()} onStartHost={vi.fn()} onStopHost={vi.fn()} />);
    expect(screen.getByText("의존성 설치 중…")).toBeInTheDocument();
    expect(screen.queryByText("빌드 완료")).not.toBeInTheDocument();
  });

  it("단계가 넘어가면 문구도 넘어간다", () => {
    const { rerender } = render(
      <PrototypeCard info={BUILT_Q} busy startingPhase="installing"
                     onBuild={vi.fn()} onStartHost={vi.fn()} onStopHost={vi.fn()} />);
    rerender(<PrototypeCard info={BUILT_Q} busy startingPhase="running"
                            onBuild={vi.fn()} onStartHost={vi.fn()} onStopHost={vi.fn()} />);
    expect(screen.getByText("서버 시작 중…")).toBeInTheDocument();
  });

  it("진행 중이 아니면 원래 상태 배지로 돌아간다", () => {
    render(<PrototypeCard info={BUILT_Q} busy={false} startingPhase={null}
                          onBuild={vi.fn()} onStartHost={vi.fn()} onStopHost={vi.fn()} />);
    expect(screen.getByText("빌드 완료")).toBeInTheDocument();
    expect(screen.queryByText(/설치 중|시작 중/)).not.toBeInTheDocument();
  });
});
