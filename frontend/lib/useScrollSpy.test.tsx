import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useScrollSpy } from "./useScrollSpy";

function Probe({ ids, enabled }: { ids: string[]; enabled?: boolean }) {
  const active = useScrollSpy(ids, enabled);
  return <span data-testid="active">{active ?? "none"}</span>;
}

/** IntersectionObserver 스텁. 마지막으로 만들어진 콜백을 직접 부를 수 있게 둔다. */
function stubObserver() {
  const state: {
    callback: IntersectionObserverCallback | null;
    observed: string[];
    disconnected: boolean;
  } = { callback: null, observed: [], disconnected: false };

  class Stub {
    constructor(cb: IntersectionObserverCallback) {
      state.callback = cb;
    }
    observe(el: Element) {
      state.observed.push(el.id);
    }
    disconnect() {
      state.disconnected = true;
    }
    unobserve() {}
    takeRecords() {
      return [];
    }
  }
  vi.stubGlobal("IntersectionObserver", Stub);
  return state;
}

/** 콜백에 넘길 최소한의 엔트리. 훅은 target.id와 isIntersecting만 읽는다. */
function entry(id: string, isIntersecting: boolean) {
  return { target: { id }, isIntersecting } as unknown as IntersectionObserverEntry;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("useScrollSpy", () => {
  it("IntersectionObserver가 없으면 던지지 않고 null을 준다", () => {
    // jsdom 기본 상태가 바로 이것이다. 여기서 던지면 매뉴얼 전체가 렌더되지
    // 않으므로, 강조를 잃는 쪽으로 떨어져야 한다.
    vi.stubGlobal("IntersectionObserver", undefined);
    render(<Probe ids={["a", "b"]} />);
    expect(screen.getByTestId("active")).toHaveTextContent("none");
  });

  it("존재하는 앵커만 관찰한다", () => {
    const state = stubObserver();
    document.body.innerHTML = `<div id="a"></div><div id="c"></div>`;
    render(<Probe ids={["a", "b", "c"]} />);
    // "b"는 DOM에 없다 — 검색으로 그 절이 걸러진 상태가 실제로 그렇다.
    expect(state.observed).toEqual(["a", "c"]);
  });

  it("문서 순서로 첫 번째로 보이는 앵커를 고른다", () => {
    const state = stubObserver();
    document.body.innerHTML = `<div id="a"></div><div id="b"></div><div id="c"></div>`;
    render(<Probe ids={["a", "b", "c"]} />);

    // 콜백은 바뀐 것만 담고 순서를 보장하지 않는다. entries 순서로 고르면
    // 여기서 "c"가 켜진다 — 위로 스크롤할 때 아래쪽 절이 강조되는 버그다.
    act(() => {
      state.callback?.([entry("c", true), entry("b", true)], {} as IntersectionObserver);
    });
    expect(screen.getByTestId("active")).toHaveTextContent("b");
  });

  it("보이지 않게 된 앵커는 후보에서 빠진다", () => {
    const state = stubObserver();
    document.body.innerHTML = `<div id="a"></div><div id="b"></div>`;
    render(<Probe ids={["a", "b"]} />);

    act(() => {
      state.callback?.([entry("a", true)], {} as IntersectionObserver);
    });
    expect(screen.getByTestId("active")).toHaveTextContent("a");

    act(() => {
      state.callback?.([entry("a", false), entry("b", true)], {} as IntersectionObserver);
    });
    expect(screen.getByTestId("active")).toHaveTextContent("b");
  });

  it("enabled가 false면 관찰하지 않는다", () => {
    const state = stubObserver();
    document.body.innerHTML = `<div id="a"></div>`;
    render(<Probe ids={["a"]} enabled={false} />);
    expect(state.observed).toEqual([]);
    expect(screen.getByTestId("active")).toHaveTextContent("none");
  });

  it("언마운트하면 관찰을 끊는다", () => {
    const state = stubObserver();
    document.body.innerHTML = `<div id="a"></div>`;
    const view = render(<Probe ids={["a"]} />);
    view.unmount();
    expect(state.disconnected).toBe(true);
  });
});
