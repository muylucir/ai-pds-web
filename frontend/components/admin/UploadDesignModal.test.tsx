import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UploadDesignModal } from "./UploadDesignModal";

vi.mock("@/lib/api/design", () => ({
  uploadDesignProfile: vi.fn(),
  // 컴포넌트가 항상 서식 다운로드 링크를 그리므로 상수도 목에 있어야 한다.
  DESIGN_TEMPLATE_PATH: "/api/admin/design/template",
}));
import { uploadDesignProfile } from "@/lib/api/design";

describe("UploadDesignModal", () => {
  it("shows the server's line-level error in place", async () => {
    const { ApiError } = await import("@/lib/api/client");
    vi.mocked(uploadDesignProfile).mockRejectedValue(
      new ApiError(400, "line 3: unknown token 'brand'"));
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);

    const input = screen.getByLabelText(/DESIGN\.md/i);
    await userEvent.upload(input,
      new File(["# x"], "acme.md", { type: "text/markdown" }));
    await userEvent.click(screen.getByRole("button", { name: /업로드|Upload/ }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("line 3"));
  });

  it("warns that replacing keeps no history", () => {
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);
    expect(screen.getByText(/남지 않습니다|does not keep/)).toBeInTheDocument();
  });
});
