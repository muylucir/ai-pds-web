import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The LanguageSwitcher that AppHeader draws calls useRouter() — in unit tests where the app router
// is not mounted, that hook throws (same reason and same mock as
// app/admin/users/page.test.tsx).
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
import { deleteDesignProfile, getDesignProfile } from "@/lib/api/design";

// The i18n provider wrapping follows the helper of the existing admin page test
// (app/admin/users/page.test.tsx) as is.
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

  // remove() exists only in page.tsx (DesignProfileCard only knows that its onRemove prop is
  // called) and calls window.confirm and deleteDesignProfile directly. This confirmation dialog
  // must carry one of the two mandatory messages the spec nailed down (removal → returns to the
  // default theme on the next re-hosting), so here we directly measure that the message actually
  // reaches confirm and leads to delete → refetch.
  it("confirms with the removal warning, then deletes and reloads", async () => {
    const profile = {
      filename: "acme.md", uploaded_at: "2026-08-15T04:12:00+00:00",
      uploaded_by: "admin@x", tokens: { primary: "#5b2ea6" }, prose: "톤",
    };
    vi.mocked(getDesignProfile)
      .mockResolvedValueOnce(profile)   // initial load
      .mockResolvedValueOnce(null);     // reload() after deletion
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminDesignPage />);
    await waitFor(() => expect(screen.getByText("acme.md")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /제거|Remove/ }));

    // Whether a real warning message was carried, not a perfunctory confirm — this assertion catches it
    // even if the i18n key changes or is replaced with the wrong message.
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.stringMatching(/재호스팅|next time they are hosted/));
    await waitFor(() => expect(deleteDesignProfile).toHaveBeenCalledTimes(1));
    // After deletion, the list is fetched again — initial load (1) + reload (1) = 2.
    await waitFor(() => expect(getDesignProfile).toHaveBeenCalledTimes(2));
  });

  it("does not delete when the removal confirm is declined", async () => {
    const profile = {
      filename: "acme.md", uploaded_at: "2026-08-15T04:12:00+00:00",
      uploaded_by: "admin@x", tokens: { primary: "#5b2ea6" }, prose: "톤",
    };
    vi.mocked(getDesignProfile).mockResolvedValue(profile);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<AdminDesignPage />);
    await waitFor(() => expect(screen.getByText("acme.md")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /제거|Remove/ }));

    // Evidence that the warning is not merely perfunctory — if declined, the deletion
    // does not actually happen.
    expect(deleteDesignProfile).not.toHaveBeenCalled();
  });

  // Final review M9: remove() wrongly reported a deletion failure as admin.designLoadFailed ("불러오지
  // 못했습니다", failed to load) — what failed was the deletion, not loading. Here we directly check
  // that it was routed to the dedicated key (admin.designDeleteFailed).
  it("shows a delete-specific error when removal fails, not the load-failed message", async () => {
    const profile = {
      filename: "acme.md", uploaded_at: "2026-08-15T04:12:00+00:00",
      uploaded_by: "admin@x", tokens: { primary: "#5b2ea6" }, prose: "톤",
    };
    vi.mocked(getDesignProfile).mockResolvedValue(profile);
    vi.mocked(deleteDesignProfile).mockRejectedValue(new Error("boom"));
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminDesignPage />);
    await waitFor(() => expect(screen.getByText("acme.md")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /제거|Remove/ }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toMatch(/제거|remove/i);
  });
});
