import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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
import { deleteDesignProfile, getDesignProfile } from "@/lib/api/design";

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

  // remove()는 page.tsx에만 있고(DesignProfileCard는 onRemove prop이 불리는
  // 것만 안다) window.confirm과 deleteDesignProfile을 직접 부른다. 이
  // 확인창은 스펙이 못박은 두 필수 문구 중 하나(제거 → 다음 재호스팅에서
  // 기본 테마로 복귀)를 실어야 하므로, 그 문구가 실제로 confirm에 전달되고
  // 삭제→재조회로 이어지는지를 여기서 직접 실측한다.
  it("confirms with the removal warning, then deletes and reloads", async () => {
    const profile = {
      filename: "acme.md", uploaded_at: "2026-08-15T04:12:00+00:00",
      uploaded_by: "admin@x", tokens: { primary: "#5b2ea6" }, prose: "톤",
    };
    vi.mocked(getDesignProfile)
      .mockResolvedValueOnce(profile)   // 최초 로드
      .mockResolvedValueOnce(null);     // 삭제 뒤 reload()
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminDesignPage />);
    await waitFor(() => expect(screen.getByText("acme.md")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /제거|Remove/ }));

    // 형식적인 confirm이 아니라 진짜 경고 문구가 실렸는지 — i18n 키가 바뀌거나
    // 엉뚱한 문구로 교체돼도 이 단정이 잡아낸다.
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.stringMatching(/재호스팅|next time they are hosted/));
    await waitFor(() => expect(deleteDesignProfile).toHaveBeenCalledTimes(1));
    // 삭제 뒤 목록이 다시 조회된다 — 최초 로드(1회) + reload(1회) = 2회.
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

    // 경고가 형식적으로만 붙어 있는 것이 아니라는 증거 — 거부하면 삭제가
    // 실제로 일어나지 않는다.
    expect(deleteDesignProfile).not.toHaveBeenCalled();
  });
});
