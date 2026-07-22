import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";
import { auditEntries } from "@/test/fixtures/auditEntries";
import ReviewPage from "./page";

const params = Promise.resolve({ projectId: "pilot1" });

const DISCOVERY_PATH = "aiplc-docs/discovery/discovery-document.md";
const AUDIT_PATH = "aiplc-docs/audit.md";
const AUDIT_CONTENT = "# Audit Log\n\n감사 로그 원문입니다.";

function mockTreeAndAudit() {
  server.use(
    http.get(`${API_BASE_URL}/projects/pilot1/artifacts`, () =>
      HttpResponse.json({ artifacts: [DISCOVERY_PATH, AUDIT_PATH] }),
    ),
    http.get(`${API_BASE_URL}/projects/pilot1/files/${DISCOVERY_PATH}`, () =>
      HttpResponse.json({ content: discoveryDocument }),
    ),
    http.get(`${API_BASE_URL}/projects/pilot1/files/${AUDIT_PATH}`, () =>
      HttpResponse.json({ content: AUDIT_CONTENT }),
    ),
    http.get(`${API_BASE_URL}/projects/pilot1/audit`, () => HttpResponse.json(auditEntries)),
  );
}

describe("Review page", () => {
  it("renders the doc tree, defaults to discovery-document.md, and shows the approval gate", async () => {
    mockTreeAndAudit();
    // `use(params)` suspends on the first render because the test's plain
    // Promise.resolve(...) params (unlike Next's internally-tracked params
    // thenable) isn't pre-marked as settled. Wrapping the initial render in
    // act() lets that Suspense retry flush before we start querying/waiting.
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    // Tree renders with both files, and the default selection highlights
    // discovery-document.md.
    const docButton = await screen.findByRole("button", { name: /discovery-document\.md/ });
    expect(docButton).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /audit\.md/ })).toBeInTheDocument();
    // Default selection loads its content and the gate shows for it.
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /승인하고 다음 단계로/ })).toBeInTheDocument();
  });

  it("selecting a non-discovery-document file hides the gate and drops the DocumentPanel/VerificationSummary chrome", async () => {
    mockTreeAndAudit();
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    await screen.findByText("Press Release");

    await userEvent.click(screen.getByRole("button", { name: /audit\.md/ }));

    expect(await screen.findByText("Audit Log")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /승인하고 다음 단계로/ })).not.toBeInTheDocument();
    expect(screen.queryByText("AI 검증 요약")).not.toBeInTheDocument();
    expect(screen.queryByText("Press Release")).not.toBeInTheDocument();
  });

  it("clicking Approve POSTs {text:'승인'} to /message", async () => {
    mockTreeAndAudit();
    let body: any;
    server.use(
      http.post(`${API_BASE_URL}/projects/pilot1/message`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ events: [{ kind: "done", text: null, path: null }] });
      }),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));
    await waitFor(() => expect(body).toEqual({ text: "승인" }));
  });

  it("submitting a revision POSTs the natural-language text to /message", async () => {
    mockTreeAndAudit();
    let body: any;
    server.use(
      http.post(`${API_BASE_URL}/projects/pilot1/message`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ events: [{ kind: "done", text: null, path: null }] });
      }),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: /수정 요청/ }));
    await userEvent.type(screen.getByLabelText(/수정 요청 사항/), "FAQ에 다국어 지원 추가");
    await userEvent.click(screen.getByRole("button", { name: /수정 요청 제출/ }));
    await waitFor(() => expect(body).toEqual({ text: "FAQ에 다국어 지원 추가" }));
  });

  // Regression: a non-404 readArtifact error was previously swallowed into
  // `content.data ?? ""`, rendering the empty-document state and hiding the
  // failure. A 500 must surface as a distinct Korean load-error message, not
  // the "아직 작성된 문서가 없습니다." empty state.
  it("shows a generic load-error state when the selected file's content fails with a non-404 error", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/artifacts`, () =>
        HttpResponse.json({ artifacts: [DISCOVERY_PATH] }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/audit`, () => HttpResponse.json(auditEntries)),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    expect(
      await screen.findByText(/문서를 불러오지 못했습니다. 백엔드 연결을 확인하세요\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/아직 작성된 문서가 없습니다/)).not.toBeInTheDocument();
  });

  // Regression: the gate previously rendered whenever discovery-document.md
  // was selected, even if its content failed to load — letting a user
  // approve a document they can never actually see. The gate must stay
  // hidden while the load-error panel is showing.
  it("hides the approval gate when the discovery document's content fails to load", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/artifacts`, () =>
        HttpResponse.json({ artifacts: [DISCOVERY_PATH] }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/audit`, () => HttpResponse.json(auditEntries)),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    expect(
      await screen.findByText(/문서를 불러오지 못했습니다. 백엔드 연결을 확인하세요\./),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /승인하고 다음 단계로/ })).not.toBeInTheDocument();
  });
});

describe("Review page — width, download, status badge", () => {
  it("uses the widened layout instead of max-w-7xl", async () => {
    mockTreeAndAudit();
    let container: HTMLElement;
    await act(async () => {
      ({ container } = render(<ReviewPage params={params} />));
    });
    const main = container!.querySelector("main");
    expect(main?.className).not.toContain("max-w-7xl");
    expect(main?.className).toContain("max-w-[1720px]");
  });

  it("downloads the selected document as .md via a Blob link", async () => {
    mockTreeAndAudit();
    // jsdom은 URL.createObjectURL을 구현하지 않음 — stub으로 주입
    const createSpy = vi.fn().mockReturnValue("blob:mock");
    const revokeSpy = vi.fn();
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createSpy;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeSpy;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    await screen.findByRole("button", { name: /discovery-document\.md/ });
    const dl = await screen.findByRole("button", { name: /\.md 다운로드/ });
    await userEvent.click(dl);
    expect(createSpy).toHaveBeenCalledTimes(1);
    const blob = createSpy.mock.calls[0][0] as Blob;
    expect(blob.type).toContain("text/markdown");
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeSpy).toHaveBeenCalledWith("blob:mock");
    delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
    clickSpy.mockRestore();
  });

  it("downloads all artifacts as a zip via a Blob link", async () => {
    mockTreeAndAudit();
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/artifacts/archive`, () =>
        HttpResponse.arrayBuffer(new ArrayBuffer(4), {
          headers: { "Content-Type": "application/zip" },
        }),
      ),
    );
    // jsdom은 URL.createObjectURL을 구현하지 않음 — stub으로 주입
    const createSpy = vi.fn().mockReturnValue("blob:mock-zip");
    const revokeSpy = vi.fn();
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createSpy;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeSpy;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    await screen.findByRole("button", { name: /discovery-document\.md/ });
    const dl = await screen.findByRole("button", { name: /전체 다운로드 \(\.zip\)/ });
    await userEvent.click(dl);
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeSpy).toHaveBeenCalledWith("blob:mock-zip");
    delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
    clickSpy.mockRestore();
  });

  it("shows a document status badge from aiplc-state and explains the gate actions", async () => {
    mockTreeAndAudit();
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () =>
        HttpResponse.json({
          project_type: "Greenfield", current_stage: "Discovery Document",
          stages: [{ name: "Discovery Document", status: "in_progress", note: null }],
        })),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    expect(await screen.findByText("초안 검토 중")).toBeInTheDocument();
    // 승인/수정이 각각 무엇을 하는지 안내 문구가 게이트에 있어야 한다
    const gate = screen.getByRole("alert");
    expect(gate.textContent).toMatch(/승인.*Discovery 단계를 완료/);
    expect(gate.textContent).toMatch(/수정 요청.*AI가 문서를 고쳐/);
  });

  it("shows the approved badge when the stage is completed", async () => {
    mockTreeAndAudit();
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () =>
        HttpResponse.json({
          project_type: "Greenfield", current_stage: "Discovery Document",
          stages: [{ name: "Discovery Document", status: "completed", note: null }],
        })),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    expect(await screen.findByText("승인 완료")).toBeInTheDocument();
  });
});

describe("Review page — document list auto-refresh", () => {
  const FAQ_PATH = "aiplc-docs/discovery/faq.md";

  it("polls the artifact list and shows a newly-created document without a manual reload", async () => {
    // Tree starts with only the discovery document; a background turn (from the
    // workspace) later creates faq.md. The poll must surface it.
    let artifacts = [DISCOVERY_PATH];
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/artifacts`, () =>
        HttpResponse.json({ artifacts }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ content: discoveryDocument }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/files/${FAQ_PATH}`, () =>
        HttpResponse.json({ content: "# FAQ" }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/audit`, () => HttpResponse.json(auditEntries)),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    // faq.md is not in the tree yet.
    expect(await screen.findByRole("button", { name: /discovery-document\.md/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /faq\.md/ })).not.toBeInTheDocument();

    // Backend creates it; the 5s poll picks it up.
    artifacts = [DISCOVERY_PATH, FAQ_PATH];
    expect(
      await screen.findByRole("button", { name: /faq\.md/ }, { timeout: 8000 }),
    ).toBeInTheDocument();
    // The user's current selection (discovery-document.md) is preserved across
    // the reload — the poll must not clobber it.
    expect(screen.getByRole("button", { name: /discovery-document\.md/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  }, 12000); // > the 5s poll interval + render/settle margin
});
