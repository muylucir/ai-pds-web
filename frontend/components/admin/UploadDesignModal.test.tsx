import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UploadDesignModal } from "./UploadDesignModal";

vi.mock("@/lib/api/design", () => ({
  uploadDesignProfile: vi.fn(),
  previewDesignProfile: vi.fn(),
  // 컴포넌트가 항상 서식 다운로드 링크를 그리므로 상수도 목에 있어야 한다.
  DESIGN_TEMPLATE_PATH: "/api/admin/design/template",
}));
import { previewDesignProfile, uploadDesignProfile } from "@/lib/api/design";

const FILE = () => new File(["# x"], "acme.md", { type: "text/markdown" });

/** 파일을 고르고 1단계 버튼을 눌러 확인 화면까지 간다. */
async function toConfirmStep() {
  await userEvent.upload(screen.getByLabelText(/DESIGN\.md/i), FILE());
  await userEvent.click(screen.getByRole("button", { name: /다음|Next/ }));
}

describe("UploadDesignModal", () => {
  it("shows what was read from the document before saving anything", async () => {
    vi.mocked(previewDesignProfile).mockResolvedValue({
      tokens: { primary: "#00754a" }, origin: "extracted", warnings: [],
    });
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);

    await toConfirmStep();

    await waitFor(() =>
      expect(screen.getByLabelText("primary")).toHaveValue("#00754a"));
    // 확인 단계다 — 여기서 저장이 일어나면 관리자가 값을 볼 기회가 없다.
    expect(uploadDesignProfile).not.toHaveBeenCalled();
  });

  it("saves the value the admin corrected, not the one the model proposed", async () => {
    // 문서가 브랜드 헤딩과 CTA에 서로 다른 색을 주면 어느 것이 primary인지는
    // 문서가 답하지 않는다 — 사람이 끊는 자리가 이 입력칸이다.
    vi.mocked(previewDesignProfile).mockResolvedValue({
      tokens: { primary: "#00754a" }, origin: "extracted", warnings: [],
    });
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);
    await toConfirmStep();
    await waitFor(() => screen.getByLabelText("primary"));

    await userEvent.clear(screen.getByLabelText("primary"));
    await userEvent.type(screen.getByLabelText("primary"), "#006241");
    await userEvent.click(screen.getByRole("button", { name: /업로드|Upload/ }));

    await waitFor(() => expect(uploadDesignProfile).toHaveBeenCalledWith(
      expect.any(File), { primary: "#006241" }));
  });

  it("says the document's own tokens block wins", async () => {
    vi.mocked(previewDesignProfile).mockResolvedValue({
      tokens: { primary: "#5b2ea6" }, origin: "fence", warnings: [],
    });
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);

    await toConfirmStep();

    await waitFor(() => expect(
      screen.getByText(/이미 토큰 블록|already has a tokens block/)).toBeInTheDocument());
  });

  it("warns when no tokens were found but still allows saving the prose", async () => {
    vi.mocked(previewDesignProfile).mockResolvedValue({
      tokens: {}, origin: "none", warnings: ["no model"],
    });
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);

    await toConfirmStep();

    await waitFor(() => expect(
      screen.getByText(/찾지 못했습니다|No tokens were found/)).toBeInTheDocument());
    // 산문만 적용하는 것도 유효한 상태다 — 저장을 막지 않는다.
    expect(screen.getByRole("button", { name: /업로드|Upload/ })).toBeEnabled();
  });

  it("shows the server's line-level error in place", async () => {
    const { ApiError } = await import("@/lib/api/client");
    vi.mocked(previewDesignProfile).mockRejectedValue(
      new ApiError(400, "line 3: unknown token 'brand'"));
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);

    await toConfirmStep();

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("line 3"));
  });

  it("shows a failure from the save step in place too", async () => {
    const { ApiError } = await import("@/lib/api/client");
    vi.mocked(previewDesignProfile).mockResolvedValue({
      tokens: { primary: "#00754a" }, origin: "extracted", warnings: [],
    });
    vi.mocked(uploadDesignProfile).mockRejectedValue(
      new ApiError(413, "file with the tokens block exceeds 65536 bytes"));
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);
    await toConfirmStep();
    await waitFor(() => screen.getByLabelText("primary"));

    await userEvent.click(screen.getByRole("button", { name: /업로드|Upload/ }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("65536"));
  });

  it("warns that replacing keeps no history", () => {
    render(<UploadDesignModal onUploaded={vi.fn()} onClose={vi.fn()} replacing />);
    expect(screen.getByText(/남지 않습니다|does not keep/)).toBeInTheDocument();
  });
});
