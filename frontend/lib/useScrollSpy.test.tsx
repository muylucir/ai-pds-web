import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useScrollSpy } from "./useScrollSpy";

function Probe({ ids, enabled }: { ids: string[]; enabled?: boolean }) {
  const active = useScrollSpy(ids, enabled);
  return <span data-testid="active">{active ?? "none"}</span>;
}

/** IntersectionObserver stub. Keeps the most recently created callback so it can be called directly. */
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

/** Minimal entry to pass to the callback. The hook reads only target.id and isIntersecting. */
function entry(id: string, isIntersecting: boolean) {
  return { target: { id }, isIntersecting } as unknown as IntersectionObserverEntry;
}

/** Plant only anchors in the body. The hook reads only ids, so empty divs suffice.
 *  Why not assign a whole HTML string: even when the value is a static literal, static analysis
 *  reports that assignment pattern itself as an XSS candidate, and creating nodes is shorter. */
function mountAnchors(...ids: string[]) {
  document.body.replaceChildren(
    ...ids.map((id) => Object.assign(document.createElement("div"), { id })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("useScrollSpy", () => {
  it("IntersectionObserver가 없으면 던지지 않고 null을 준다", () => {
    // This is exactly jsdom's default state. Throwing here would keep the whole manual from
    // rendering, so it must fall back to losing the highlight.
    vi.stubGlobal("IntersectionObserver", undefined);
    render(<Probe ids={["a", "b"]} />);
    expect(screen.getByTestId("active")).toHaveTextContent("none");
  });

  it("존재하는 앵커만 관찰한다", () => {
    const state = stubObserver();
    mountAnchors("a", "c");
    render(<Probe ids={["a", "b", "c"]} />);
    // "b" is not in the DOM — that is what it actually looks like when search has filtered out that section.
    expect(state.observed).toEqual(["a", "c"]);
  });

  it("문서 순서로 첫 번째로 보이는 앵커를 고른다", () => {
    const state = stubObserver();
    mountAnchors("a", "b", "c");
    render(<Probe ids={["a", "b", "c"]} />);

    // The callback carries only what changed and does not guarantee order. Picking by entries order would
    // light up "c" here — the bug where a lower section gets highlighted while scrolling up.
    act(() => {
      state.callback?.([entry("c", true), entry("b", true)], {} as IntersectionObserver);
    });
    expect(screen.getByTestId("active")).toHaveTextContent("b");
  });

  it("보이지 않게 된 앵커는 후보에서 빠진다", () => {
    const state = stubObserver();
    mountAnchors("a", "b");
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
    mountAnchors("a");
    render(<Probe ids={["a"]} enabled={false} />);
    expect(state.observed).toEqual([]);
    expect(screen.getByTestId("active")).toHaveTextContent("none");
  });

  it("언마운트하면 관찰을 끊는다", () => {
    const state = stubObserver();
    mountAnchors("a");
    const view = render(<Probe ids={["a"]} />);
    view.unmount();
    expect(state.disconnected).toBe(true);
  });
});
