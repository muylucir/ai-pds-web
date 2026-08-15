import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// AppHeader가 그리는 LanguageSwitcher가 useRouter()를 부른다 — 앱 라우터가
// 마운트되지 않은 단위 테스트에서 그 훅은 던진다(app/admin/users/page.test.tsx와
// 같은 이유·같은 목).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

import AdminDesignPage from "./page";

vi.mock("@/lib/api/design", () => ({
  getDesignProfile: vi.fn(),
  deleteDesignProfile: vi.fn(),
  uploadDesignProfile: vi.fn(),
  DESIGN_RAW_PATH: "/api/admin/design/raw",
  DESIGN_TEMPLATE_PATH: "/api/admin/design/template",
}));
import { getDesignProfile } from "@/lib/api/design";

// i18n provider 래핑은 기존 admin 페이지 테스트(app/admin/users/page.test.tsx)의
// 헬퍼를 그대로 따른다.
beforeEach(() => vi.clearAllMocks());

describe("admin design page", () => {
  it("says prototypes use the shadcn default when no profile exists", async () => {
    vi.mocked(getDesignProfile).mockResolvedValue(null);
    render(<AdminDesignPage />);
    await waitFor(() => expect(screen.getByText(/shadcn/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /서식|template/i }))
      .toHaveAttribute("href", "/api/admin/design/template");
  });

  it("shows the token swatches and the uploader", async () => {
    vi.mocked(getDesignProfile).mockResolvedValue({
      filename: "acme.md", uploaded_at: "2026-08-15T04:12:00+00:00",
      uploaded_by: "admin@x", tokens: { primary: "#5b2ea6" }, prose: "톤",
    });
    render(<AdminDesignPage />);
    await waitFor(() => expect(screen.getByText("acme.md")).toBeInTheDocument());
    expect(screen.getByText("#5b2ea6")).toBeInTheDocument();
    expect(screen.getByText(/admin@x/)).toBeInTheDocument();
  });

  it("surfaces 403 as an admin-only message", async () => {
    const { ApiError } = await import("@/lib/api/client");
    vi.mocked(getDesignProfile).mockRejectedValue(new ApiError(403, "nope"));
    render(<AdminDesignPage />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
