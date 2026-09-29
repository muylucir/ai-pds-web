// frontend/components/canvas/ContextMeter.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ContextMeter } from "./ContextMeter";
import { ChatInput } from "./ChatInput";

const usage = (left: number) => ({ total_tokens: 285000, max_tokens: 750000,
                                   compact_at_tokens: 717000, left_pct: left });

describe("ContextMeter", () => {
  it("says how much is left until compaction, with the token counts on hover", () => {
    render(<ContextMeter usage={usage(60)} />);
    const line = screen.getByText("컨텍스트 60% 남음");
    expect(line.getAttribute("title")).toContain("285K / 717K");
  });

  it("stands out when compaction is near", () => {
    render(<ContextMeter usage={usage(8)} />);
    expect(screen.getByText("컨텍스트 8% 남음").className).toContain("text-red-600");
  });
});

describe("ChatInput context line", () => {
  it("is drawn only when there is a value", () => {
    const { rerender } = render(<ChatInput onSend={() => {}} disabled={false} context={null} />);
    expect(screen.queryByText(/컨텍스트/)).toBeNull();
    rerender(<ChatInput onSend={() => {}} disabled={false} context={usage(42)} />);
    expect(screen.getByText("컨텍스트 42% 남음")).toBeInTheDocument();
  });
});
