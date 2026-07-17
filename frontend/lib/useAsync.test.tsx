import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { useAsync } from "./useAsync";

function Probe({ fn }: { fn: () => Promise<string> }) {
  const { data, error, loading, reload } = useAsync(fn, []);
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="data">{data ?? ""}</span>
      <span data-testid="error">{error ? error.message : ""}</span>
      <button onClick={reload}>reload</button>
    </div>
  );
}

describe("useAsync", () => {
  it("goes loading → data", async () => {
    render(<Probe fn={async () => "hello"} />);
    expect(screen.getByTestId("loading").textContent).toBe("true");
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("hello"));
    expect(screen.getByTestId("loading").textContent).toBe("false");
  });

  it("captures errors", async () => {
    render(<Probe fn={async () => { throw new Error("boom"); }} />);
    await waitFor(() => expect(screen.getByTestId("error").textContent).toBe("boom"));
  });

  it("reload re-invokes fn", async () => {
    const fn = vi.fn(async () => "x");
    render(<Probe fn={fn} />);
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("x"));
    await act(async () => {
      screen.getByText("reload").click();
    });
    await waitFor(() => expect(fn.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
