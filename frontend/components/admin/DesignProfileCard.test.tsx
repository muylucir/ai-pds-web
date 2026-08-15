// frontend/components/admin/DesignProfileCard.test.tsx
//
// 도메인 파싱은 백엔드가 이미 끝낸 상태로 profile을 넘겨준다 — 이 카드는 순수
// 표시 컴포넌트다. API 호출이 없으므로 MSW 목이 필요 없다.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DesignProfileCard } from "./DesignProfileCard";
import type { DesignProfile } from "@/lib/api/design";

const BASE: DesignProfile = {
  filename: "acme.md",
  uploaded_at: "2026-08-15T04:12:00+00:00",
  uploaded_by: "admin@x",
  tokens: { primary: "#5b2ea6" },
  prose: "여백을 넉넉히 쓴다.",
};

describe("DesignProfileCard", () => {
  it("shows the filename, uploader and a link to the raw download", () => {
    render(<DesignProfileCard profile={BASE} onReplace={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByText("acme.md")).toBeInTheDocument();
    expect(screen.getByText(/admin@x/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /원문 내려받기|Download original/ }))
      .toHaveAttribute("href", "/api/admin/design/raw");
  });

  it("draws a colour swatch only for hex-shaped tokens", () => {
    render(<DesignProfileCard
      profile={{ ...BASE, tokens: { primary: "#5b2ea6", radius: "0.75rem" } }}
      onReplace={vi.fn()} onRemove={vi.fn()} />);
    // primary는 hex라 스와치가 붙고, radius는 길이값이라 붙지 않는다.
    expect(screen.getByText("primary").closest("li")?.querySelector("[aria-hidden]"))
      .not.toBeNull();
    expect(screen.getByText("radius").closest("li")?.querySelector("[aria-hidden]"))
      .toBeNull();
  });

  it("renders without breaking when there are no tokens", () => {
    render(<DesignProfileCard profile={{ ...BASE, tokens: {} }}
                              onReplace={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByText("acme.md")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("does not leave an empty collapsible section when there is no prose", () => {
    render(<DesignProfileCard profile={{ ...BASE, prose: "" }}
                              onReplace={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.queryByText(/지침|Guidance/)).not.toBeInTheDocument();
  });

  it("shows the prose in a collapsible section when present", () => {
    render(<DesignProfileCard profile={BASE} onReplace={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByText(/지침|Guidance/)).toBeInTheDocument();
    expect(screen.getByText("여백을 넉넉히 쓴다.")).toBeInTheDocument();
  });

  it("calls onReplace and onRemove", async () => {
    const onReplace = vi.fn();
    const onRemove = vi.fn();
    render(<DesignProfileCard profile={BASE} onReplace={onReplace} onRemove={onRemove} />);
    await userEvent.click(screen.getByRole("button", { name: /교체|Replace/ }));
    await userEvent.click(screen.getByRole("button", { name: /제거|Remove/ }));
    expect(onReplace).toHaveBeenCalledTimes(1);
    expect(onRemove).toHaveBeenCalledTimes(1);
  });
});
