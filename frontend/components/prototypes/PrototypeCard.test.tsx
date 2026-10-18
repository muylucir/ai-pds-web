import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PrototypeCard } from "./PrototypeCard";
import type { PrototypeInfo } from "@/lib/api/prototypes";

function info(overrides: Partial<PrototypeInfo>): PrototypeInfo {
  return {
    slug: "todo-app",
    name: null,
    spec_path: "aiplc-docs/discovery/prototypes/todo-app/PROTOTYPE-todo-app.md",
    state: "none",
    port: null,
    session_open: false,
    preview_stale: false,
    access_url: null,
    response_count: 0,
    has_survey: false,
    ...overrides,
  };
}

const noop = {
  onBuild: vi.fn(),
  onStartHost: vi.fn(),
  onStopHost: vi.fn(),
};

describe("PrototypeCard", () => {
  it("제목: 명세에서 읽은 이름을 보여준다", () => {
    render(<PrototypeCard info={info({ name: "기획전 AI 어시스턴트" })} busy={false} {...noop} />);
    expect(screen.getByText("기획전 AI 어시스턴트")).toBeInTheDocument();
  });

  it("제목: 이름이 없으면 슬러그로 되돌아간다", () => {
    // Real Path B artifacts have no name line. The slug is still a readable
    // value then (`todo-app`), so it beats leaving the title empty.
    render(<PrototypeCard info={info({ name: null })} busy={false} {...noop} />);
    expect(screen.getByText("todo-app")).toBeInTheDocument();
  });

  it("제목: 단일 프로토타입의 예약 슬러그를 제목으로 쓰지 않는다", () => {
    // This is the very symptom this card set out to fix. The Path A.1 slug is the
    // constant "prototype" (SINGLE_ID in the backend's proto/layout.py), so every
    // project's card had the same word as its title.
    render(
      <PrototypeCard
        info={info({ slug: "prototype", name: "재고 예측 어시스턴트" })}
        busy={false}
        {...noop}
      />,
    );
    expect(screen.getByText("재고 예측 어시스턴트")).toBeInTheDocument();
    expect(screen.queryByText("prototype")).not.toBeInTheDocument();
  });

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

  it("built: shows 빌드 완료 badge plus 호스팅 시작 and 수정하기 buttons", async () => {
    // **Not "다시 빌드" (rebuild).** This button discards nothing -- a completed
    // build opens an improvement session carrying only the summary (the handoff
    // branch in the backend's proto/session). The former name was the opposite of
    // what it does, and since it sat right next to `초기화` (reset), a safe action read as
    // destructive -- a user who wanted to make changes had nothing to press.
    const user = userEvent.setup();
    const onStartHost = vi.fn();
    const onBuild = vi.fn();
    render(
      <PrototypeCard info={info({ state: "built" })} busy={false} {...noop} onStartHost={onStartHost} onBuild={onBuild} />,
    );
    expect(screen.getByText("빌드 완료")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "호스팅 시작" }));
    expect(onStartHost).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "수정하기" }));
    expect(onBuild).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "다시 빌드" })).toBeNull();
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

  // Copy link. It is shown only while hosting because only then does the link
  // work -- in the built state the backend returns 502 for the link
  // (routes/proto_public.py). We do not let users share a broken link.
  describe("링크 복사", () => {
    // userEvent.setup() installs navigator.clipboard as a getter, so
    // Object.assign throws "has only a getter". We override it with
    // defineProperty and restore the original descriptor in afterEach --
    // otherwise this stub pollutes the next test's userEvent too.
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

    // No fake timers: userEvent uses timers of its own, so the combination hangs
    // waiting on the click, and the leftover real timers drag into the next test
    // (measured: this test and the two after it hit the 5s timeout). We only need
    // to confirm that it goes back, so the 2 seconds are left to findBy*'s polling.
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

      // The label comes back after 2 seconds -- that is what makes a second copy distinguishable on screen.
      expect(await screen.findByRole("button", { name: "링크 복사" }, { timeout: 3000 }))
        .toBeInTheDocument();
    });

    it("does not claim success when the clipboard is unavailable", async () => {
      const user = userEvent.setup();
      // Non-HTTPS origin or permission denied -- silently pretending to succeed
      // would have the user paste an empty clipboard.
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

  it("failed: shows a rose 실패 badge plus 이어서 하기 and 로그", () => {
    // "수정" (modify) is awkward for a failed build -- there is nothing to modify
    // yet; what is needed is to finish. The backend also takes the resume branch
    // then and asks "what to continue".
    render(<PrototypeCard info={info({ state: "failed" })} busy={false} {...noop} onShowLogs={vi.fn()} />);
    const badge = screen.getByText("실패");
    expect(badge.className).toContain("rose");
    expect(screen.getByRole("button", { name: "이어서 하기" })).toBeInTheDocument();
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

// ---- Modifying an already-built prototype ----
//
// The first build was easy, but changing something once built was awkward. Two causes.
//
// One was the name: the `built` button said "다시 빌드" (rebuild), but that action
// discards nothing (the backend opens an improvement session carrying only the
// summary). The name was the opposite of what it does, and right next to `초기화`
// (reset), a safe action read as destructive.
//
// The other was bigger: `running` had **no build-related buttons at all**. The moment
// you want to change something is right after seeing the preview, yet there was no
// path on screen at that moment; the button came back only after stopping hosting first.

describe("PrototypeCard — 수정 경로", () => {
  it("실행 중에도 수정할 수 있다", () => {
    // Right after seeing the preview is when you want to change things. The screen must offer a path at that moment.
    const onBuild = vi.fn();
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4007 })}
        busy={false}
        {...noop}
        onBuild={onBuild}
      />,
    );
    expect(screen.getByRole("button", { name: "수정하기" })).toBeInTheDocument();
    // It can be pressed without stopping hosting.
    expect(screen.getByRole("button", { name: "호스팅 중지" })).toBeInTheDocument();
  });

  it("수정 세션이 열려 있으면 그 세션으로 돌아가는 버튼이 된다", () => {
    // Labeling an already-open session "수정하기" (modify) reads as if it opens a new one.
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4007, session_open: true })}
        busy={false}
        {...noop}
      />,
    );
    expect(screen.getByRole("button", { name: "세션 열기" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정하기" })).toBeNull();
  });

  it("수정 중에도 프리뷰와 공유 링크를 잃지 않는다", () => {
    // Opening a session does not touch hosting -- the server stays up and the link
    // sent to participants stays live. Previously the card switched to building and
    // both disappeared.
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4007, session_open: true })}
        busy={false}
        {...noop}
        onOpenPreview={vi.fn()}
        shareUrl="https://example.com/api/proto/t/tok"
      />,
    );
    expect(screen.getByRole("button", { name: "프리뷰 열기" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "링크 복사" })).toBeInTheDocument();
  });

  it("낡은 프리뷰임을 말하고, 고치는 대가까지 함께 말한다", () => {
    // Hosting reruns npm install → build → server start on the existing tree (measured
    // up to 13 minutes). The participant link is closed during that time too, so this
    // is something to know before pressing.
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4007, preview_stale: true })}
        busy={false}
        {...noop}
      />,
    );
    expect(screen.getByText(/프리뷰는 이전 버전입니다/)).toBeInTheDocument();
    expect(screen.getByText(/참가자 링크까지 몇 분간 닫힙니다/)).toBeInTheDocument();
  });

  it("낡았을 때만 다시 호스팅 버튼이 나온다", () => {
    const onStartHost = vi.fn();
    const { rerender } = render(
      <PrototypeCard info={info({ state: "running", port: 4007 })} busy={false} {...noop} />,
    );
    expect(screen.queryByRole("button", { name: "다시 호스팅" })).toBeNull();

    rerender(
      <PrototypeCard
        info={info({ state: "running", port: 4007, preview_stale: true })}
        busy={false}
        {...noop}
        onStartHost={onStartHost}
      />,
    );
    screen.getByRole("button", { name: "다시 호스팅" }).click();
    expect(onStartHost).toHaveBeenCalledTimes(1);
  });

  it("경고가 세션 수명에 매이지 않는다", () => {
    // **This is why the criterion changed.** Keyed to the session, the warning would
    // vanish the moment the session closes, which is exactly when the user is most
    // likely to wrongly assume "it has been applied".
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4007,
                     session_open: false, preview_stale: true })}
        busy={false}
        {...noop}
      />,
    );
    expect(screen.getByText(/프리뷰는 이전 버전입니다/)).toBeInTheDocument();
  });

  it("낡지 않았으면 그 경고를 띄우지 않는다", () => {
    render(
      <PrototypeCard
        info={info({ state: "running", port: 4007, session_open: true })}
        busy={false}
        {...noop}
      />,
    );
    expect(screen.queryByText(/프리뷰는 이전 버전입니다/)).toBeNull();
  });

  it("아직 빌드하지 않은 프로토타입에는 수정 버튼이 없다", () => {
    // At a stage with nothing to modify, there is no telling what that button would do.
    render(<PrototypeCard info={info({ state: "none" })} busy={false} {...noop} />);
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정하기" })).toBeNull();
  });

  it("버튼이 여덟까지 늘어나도 카드 밖으로 넘치지 않는다", () => {
    // running is the busiest: preview, link, modify, stop, logs, download, reset, survey.
    const { container } = render(
      <PrototypeCard
        info={info({ state: "running", port: 4007 })}
        busy={false}
        {...noop}
        onOpenPreview={vi.fn()}
        onShowLogs={vi.fn()}
        onOpenSurvey={vi.fn()}
        onReset={vi.fn()}
        shareUrl="https://example.com/api/proto/t/tok"
        archiveUrl="/archive.zip"
      />,
    );
    const row = container.querySelector("div.flex.flex-wrap");
    expect(row).not.toBeNull();
  });
});

// ---- Progress display while hosting starts (2026-08-19) ----
// `POST /host` responds only after awaiting all of npm install → npm run build →
// port wait (up to 60 seconds). Meanwhile the card sat frozen at "build complete +
// disabled buttons", and since the stretch measured 13 minutes, users read it as
// "no response". The server already records those stages (state in
// ProtoHost._registry), so we only need to show them.

const BUILT_Q: PrototypeInfo = {
  slug: "prototype",
  name: null,
  spec_path: "aiplc-docs/discovery/prototype/prototype-spec.md",
  state: "built",
  port: null,
  session_open: false,
  preview_stale: false,
  access_url: null,
  response_count: 0,
  has_survey: false,
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


describe("PrototypeCard — 설문 표시", () => {
  // Observed in ws-n: only 1 of 3 prototypes had a survey, but the card did not
  // say so, leaving no way to notice the other two were missing one. `response_count`
  // alone cannot express it -- no survey is 0, and zero responses is also 0.

  it("설문이 없으면 '설문 없음'을 보여준다", () => {
    render(<PrototypeCard info={info({ state: "built", has_survey: false })}
                          busy={false} {...noop} />);
    expect(screen.getByText("설문 없음")).toBeInTheDocument();
  });

  it("설문이 있으면 응답 수를 보여준다", () => {
    render(<PrototypeCard info={info({ state: "built", has_survey: true, response_count: 3 })}
                          busy={false} {...noop} />);
    expect(screen.getByText("설문 · 응답 3건")).toBeInTheDocument();
    expect(screen.queryByText("설문 없음")).not.toBeInTheDocument();
  });

  it("설문이 있고 응답이 0건이어도 '설문 없음'이 아니다", () => {
    render(<PrototypeCard info={info({ state: "built", has_survey: true, response_count: 0 })}
                          busy={false} {...noop} />);
    expect(screen.getByText("설문 · 응답 0건")).toBeInTheDocument();
    expect(screen.queryByText("설문 없음")).not.toBeInTheDocument();
  });

  it("빌드 전 프로토타입에는 설문 표시를 걸지 않는다", () => {
    // At a stage with nothing built yet, "설문 없음" (no survey) reads like a to-do and becomes noise.
    render(<PrototypeCard info={info({ state: "none", has_survey: false })}
                          busy={false} {...noop} />);
    expect(screen.queryByText("설문 없음")).not.toBeInTheDocument();
  });

  it("열람 멤버: 프리뷰·링크·다운로드·설문은 남고 빌드·호스팅·초기화는 없다", () => {
    render(
      <PrototypeCard info={info({ state: "running", port: 4100, access_url: "/proto/t/x" })}
                     busy={false} readOnly {...noop} onOpenPreview={vi.fn()} onReset={vi.fn()}
                     onOpenSurvey={vi.fn()} onShowLogs={vi.fn()} archiveUrl="/a.zip"
                     shareUrl="https://example.com/proto/t/x" />,
    );
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["프리뷰 열기", "링크 복사", "로그", "설문"]);
    expect(screen.getByRole("link", { name: "다운로드" })).toBeInTheDocument();
  });

  it("열람 멤버: 빌드 전에는 누를 것이 없다", () => {
    render(<PrototypeCard info={info({ state: "none" })} busy={false} readOnly {...noop} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});

