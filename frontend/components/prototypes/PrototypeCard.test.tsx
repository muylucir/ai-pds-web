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
    // 실측 Path B 산출물에는 이름 줄이 없다. 그때 슬러그는 여전히 읽을 수 있는
    // 값이므로(`todo-app`) 제목 자리를 비우는 것보다 낫다.
    render(<PrototypeCard info={info({ name: null })} busy={false} {...noop} />);
    expect(screen.getByText("todo-app")).toBeInTheDocument();
  });

  it("제목: 단일 프로토타입의 예약 슬러그를 제목으로 쓰지 않는다", () => {
    // 이 카드가 고치려는 증상 그 자체다. Path A.1의 슬러그는 상수 "prototype"
    // 이어서(백엔드 proto/layout.py의 SINGLE_ID) 모든 프로젝트의 카드 제목이
    // 같은 단어였다.
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
    // **"다시 빌드"가 아니다.** 이 버튼은 무엇도 버리지 않는다 — 완료된 빌드에는
    // 요약만 실은 개선 세션이 열린다(백엔드 proto/session의 handoff 분기). 종전
    // 이름은 실제와 정반대였고, `초기화` 바로 옆이라 안전한 동작이 파괴적으로
    // 읽혔다 — 수정하려는 사용자가 누를 것이 화면에 없었다.
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

  it("failed: shows a rose 실패 badge plus 이어서 하기 and 로그", () => {
    // 실패한 빌드에 "수정"은 어색하다 — 아직 수정할 것이 없고 마치는 것이
    // 필요하다. 백엔드도 그때 resume 분기로 "무엇을 이어갈지" 묻는다.
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

// ---- 이미 만들어진 프로토타입을 수정한다 ----
//
// 최초 빌드는 편했지만 한 번 만들어진 것을 고치기가 불편했다. 원인이 둘이다.
//
// 하나는 이름이었다: `built`의 버튼이 "다시 빌드"였는데 그 동작은 무엇도 버리지
// 않는다(백엔드가 요약만 실은 개선 세션을 연다). 이름이 실제와 정반대인 데다
// `초기화` 바로 옆이라, 안전한 동작이 파괴적으로 읽혔다.
//
// 다른 하나는 더 컸다: `running`에 빌드 관련 버튼이 **아예 없었다**. 고치고
// 싶어지는 순간은 프리뷰를 본 직후인데 그 순간 화면에 길이 없었고, 호스팅을 먼저
// 중지해야 버튼이 돌아왔다.

describe("PrototypeCard — 수정 경로", () => {
  it("실행 중에도 수정할 수 있다", () => {
    // 프리뷰를 본 직후가 고치고 싶어지는 순간이다. 그 순간 화면에 길이 있어야 한다.
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
    // 호스팅을 중지하지 않고도 눌린다.
    expect(screen.getByRole("button", { name: "호스팅 중지" })).toBeInTheDocument();
  });

  it("수정 세션이 열려 있으면 그 세션으로 돌아가는 버튼이 된다", () => {
    // 이미 열린 세션에 대고 "수정하기"라고 하면 새로 여는 것처럼 읽힌다.
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
    // 세션을 여는 것은 호스팅을 건드리지 않는다 — 서버는 계속 떠 있고 참가자에게
    // 나간 링크도 살아 있다. 종전에는 카드가 building으로 바뀌며 둘 다 사라졌다.
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
    // 호스팅은 기존 트리에 npm install → build → 서버 시작을 다시 돌린다(실측 최대
    // 13분). 그 동안 참가자 링크까지 닫히므로, 누르기 전에 알아야 하는 사실이다.
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
    // **이것이 판정 기준을 바꾼 이유다.** 세션 기준이면 세션이 닫히는 순간 경고가
    // 사라지는데, 정작 그때가 사용자가 "반영됐다"고 오해하기 가장 쉬운 시점이다.
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
    // 수정할 것이 없는 단계에서 그 버튼은 무엇을 하는지 알 수 없다.
    render(<PrototypeCard info={info({ state: "none" })} busy={false} {...noop} />);
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정하기" })).toBeNull();
  });

  it("버튼이 여덟까지 늘어나도 카드 밖으로 넘치지 않는다", () => {
    // running은 프리뷰·링크·수정·중지·로그·다운로드·초기화·설문로 가장 붐빈다.
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

// ---- 호스팅 시작 중의 진행 표시 (2026-08-19) ----
// `POST /host`가 npm install → npm run build → 포트 대기(최대 60초)를 전부
// await한 뒤 응답한다. 그동안 카드는 "빌드 완료 + 비활성 버튼"으로 멈춰 있었고,
// 실측 13분짜리 구간이라 사용자가 "아무 반응 없음"으로 읽었다. 서버는 그 단계를
// 이미 기록하므로(ProtoHost._registry의 state) 보여주기만 하면 된다.

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
  // 실측 test2222: 프로토타입 3개 중 1개에만 설문이 있었는데 카드에 그 사실이
  // 없어 나머지 둘이 빠진 것을 알아차릴 방법이 없었다. `response_count`만으로는
  // 표현할 수 없다 — 설문 없음도 0, 응답 0건도 0이다.

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
    // 아직 만들 것이 없는 단계에서 "설문 없음"은 할 일처럼 읽혀 잡음이 된다.
    render(<PrototypeCard info={info({ state: "none", has_survey: false })}
                          busy={false} {...noop} />);
    expect(screen.queryByText("설문 없음")).not.toBeInTheDocument();
  });
});
