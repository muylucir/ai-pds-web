import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UploadDesignModal } from "./UploadDesignModal";

vi.mock("@/lib/api/design", () => ({
  uploadDesignProfile: vi.fn(),
  previewDesignProfile: vi.fn(),
  // The component always draws the template download link, so the constant must be in the mock too.
  DESIGN_TEMPLATE_PATH: "/api/admin/design/template",
}));
import { previewDesignProfile, uploadDesignProfile } from "@/lib/api/design";

const FILE = () => new File(["# x"], "acme.md", { type: "text/markdown" });

/** Pick a file and press the step 1 button to get to the confirmation screen. */
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
    // This is the confirmation step — if saving happened here, the admin would have no chance to see the values.
    expect(uploadDesignProfile).not.toHaveBeenCalled();
  });

  it("saves the value the admin corrected, not the one the model proposed", async () => {
    // When the document gives different colors to the brand heading and the CTA, the document does not
    // answer which one is primary — this input field is where a person decides.
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
    // Applying only the prose is also a valid state — it does not block saving.
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
