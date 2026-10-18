import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";
import { auditEntries } from "@/test/fixtures/auditEntries";

// The LanguageSwitcher that AppHeader draws calls useRouter() — in a unit test with no app router
// mounted, that hook throws. The switcher's behavior is verified by
// components/LanguageSwitcher.test.tsx, so here it only needs to mount.
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: pushMock }),
}));

import ReviewPage from "./page";

const params = Promise.resolve({ projectId: "ws-m" });

const DISCOVERY_PATH = "aiplc-docs/discovery/discovery-document.md";
const AUDIT_PATH = "aiplc-docs/audit.md";
const AUDIT_CONTENT = "# Audit Log\n\n감사 로그 원문입니다.";

function mockTreeAndAudit() {
  server.use(
    http.get(`${API_BASE_URL}/projects/ws-m/artifacts`, () =>
      HttpResponse.json({ artifacts: [DISCOVERY_PATH, AUDIT_PATH] }),
    ),
    http.get(`${API_BASE_URL}/projects/ws-m/files/${DISCOVERY_PATH}`, () =>
      HttpResponse.json({ content: discoveryDocument }),
    ),
    http.get(`${API_BASE_URL}/projects/ws-m/files/${AUDIT_PATH}`, () =>
      HttpResponse.json({ content: AUDIT_CONTENT }),
    ),
    http.get(`${API_BASE_URL}/projects/ws-m/audit`, () => HttpResponse.json(auditEntries)),
    // Approval history — the primary basis for the gate judgment (lib/approvalState.ts). The
    // default is "not yet approved", so the gate is showing.
    http.get(`${API_BASE_URL}/projects/ws-m/approvals`, () =>
      HttpResponse.json({ approvals: [], current_doc_hash: "h-current" }),
    ),
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

  it("warns before download when the discovery document cites files that are missing", async () => {
    // The shape measured in ws-l: Part 2 points to an evidence file that is not in the
    // artifacts.
    mockTreeAndAudit();
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts/missing-references`, () =>
        HttpResponse.json({ document: DISCOVERY_PATH,
                            missing: ["prototype/validation-results.md"] }),
      ),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("1");
    expect(alert).toHaveTextContent("prototype/validation-results.md");

    // Viewing another document clears the warning — this check is about the entry document.
    await userEvent.click(screen.getByRole("button", { name: /audit\.md/ }));
    await screen.findByText("Audit Log");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows no warning when every cited file exists", async () => {
    mockTreeAndAudit();
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    await screen.findByText("Press Release");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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

  it("clicking Approve POSTs to /approve, not a chat message", async () => {
    // Replaces sending "승인" (approve) text as a chat turn. On that path the only record of the
    // approval was the audit.md the agent writes, and if the agent transcribed the wording
    // differently, the fact that the user pressed the button vanished — measured, 3 of 5 approval
    // gates were not recognized (lib/approvalState.ts header).
    //
    // The approval text and target document hash are now decided by the backend. Trusting values
    // the screen sent could approve content different from (stale relative to) what the user saw.
    mockTreeAndAudit();
    let approveCalls = 0;
    let messageCalls = 0;
    server.use(
      http.post(`${API_BASE_URL}/projects/ws-m/approve`, () => {
        approveCalls += 1;
        return HttpResponse.json({ approved: true, turn_id: "t-approve" });
      }),
      http.post(`${API_BASE_URL}/projects/ws-m/turns`, () => {
        messageCalls += 1;
        return HttpResponse.json({ turn_id: "t-1" });
      }),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));

    await waitFor(() => expect(approveCalls).toBe(1));
    expect(messageCalls).toBe(0);
    // The next stage's turn is shown by the workspace — approval does not wait for that turn.
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/projects/ws-m/workspace"));
  });

  it("AI가 아직 작업 중이면 승인을 거절하고 그 이유를 말한다", async () => {
    mockTreeAndAudit();
    pushMock.mockClear();
    server.use(
      http.post(`${API_BASE_URL}/projects/ws-m/approve`, () =>
        HttpResponse.json({ detail: { code: "turn_in_progress", turn_id: "t-run" } },
                          { status: 409 })),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));

    expect(await screen.findByText(/작업이 끝난 뒤 승인/)).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("승인 레코드가 있고 해시가 일치하면 게이트가 뜨지 않는다", async () => {
    // It is judged regardless of the audit log's wording — this is the core of this feature.
    mockTreeAndAudit();
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/approvals`, () =>
        HttpResponse.json({
          approvals: [{ document: DISCOVERY_PATH, doc_hash: "h-current",
                        approved_at: "2026-08-10T01:00:00Z" }],
          current_doc_hash: "h-current",
        }),
      ),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /승인하고 다음 단계로/ })).not.toBeInTheDocument());
  });

  it("승인 이후 문서가 바뀌면(해시 불일치) 게이트가 다시 뜬다", async () => {
    mockTreeAndAudit();
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/approvals`, () =>
        HttpResponse.json({
          approvals: [{ document: DISCOVERY_PATH, doc_hash: "h-old",
                        approved_at: "2026-08-10T01:00:00Z" }],
          current_doc_hash: "h-current",
        }),
      ),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    expect(await screen.findByRole("button", { name: /승인하고 다음 단계로/ })).toBeInTheDocument();
  });

  it("수정 요청 링크는 워크스페이스 채팅으로 이동하며 문서명이 담긴 초안을 ?draft=로 전달한다", async () => {
    mockTreeAndAudit();
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    await screen.findByText("Press Release");
    const link = screen.getByRole("link", { name: /수정 요청/ });
    expect(link).toHaveAttribute(
      "href",
      `/projects/ws-m/workspace?draft=${encodeURIComponent("discovery-document.md 수정 요청: ")}`,
    );
  });

  // Regression: a non-404 readArtifact error was previously swallowed into
  // `content.data ?? ""`, rendering the empty-document state and hiding the
  // failure. A 500 must surface as a distinct Korean load-error message, not
  // the "아직 작성된 문서가 없습니다." empty state.
  it("shows a generic load-error state when the selected file's content fails with a non-404 error", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts`, () =>
        HttpResponse.json({ artifacts: [DISCOVERY_PATH] }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/audit`, () => HttpResponse.json(auditEntries)),
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
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts`, () =>
        HttpResponse.json({ artifacts: [DISCOVERY_PATH] }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/audit`, () => HttpResponse.json(auditEntries)),
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
    // jsdom does not implement URL.createObjectURL — inject it as a stub
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
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts/archive`, () =>
        HttpResponse.arrayBuffer(new ArrayBuffer(4), {
          headers: { "Content-Type": "application/zip" },
        }),
      ),
    );
    // jsdom does not implement URL.createObjectURL — inject it as a stub
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
      http.get(`${API_BASE_URL}/projects/ws-m/state`, () =>
        HttpResponse.json({
          project_type: "Greenfield", current_stage: "Discovery Document",
          stages: [{ name: "Discovery Document", status: "in_progress", note: null }],
        })),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    expect(await screen.findByText("초안 검토 중")).toBeInTheDocument();
    // The gate must have guidance text on what approve and revise each do.
    //
    // Find it by a named region — this banner is a resident area, not a sudden event
    // (the role comment in ApprovalGate).
    const gate = screen.getByRole("region", { name: "승인 게이트" });
    // **The wording must not name a specific stage.** The former assertion required
    // "Discovery 단계를 완료" (complete the Discovery stage), but this banner always appears when
    // discovery-document.md exists and is unapproved, and the document is created by Envision
    // (upstream envision.md:255). That is, it was saying "Discovery 완료" (Discovery complete)
    // even at Envision approval time. In fact this button passes whatever gate the agent is
    // sitting at.
    expect(gate.textContent).toMatch(/승인.*다음 단계로 넘어갑니다/);
    expect(gate.textContent).not.toMatch(/최종|Discovery 단계를 완료/);
    expect(gate.textContent).toMatch(/수정 요청.*워크스페이스 채팅으로 이동/);
  });

  it("hides the gate and shows a completion banner once approved", async () => {
    // Approval state comes from the AUDIT LOG, not aiplc-state: nothing in the
    // rules or the agent ever writes a "Discovery Document" stage, so the old
    // stage lookup was always undefined and the gate never went away.
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts`, () =>
        HttpResponse.json({ artifacts: [DISCOVERY_PATH, AUDIT_PATH] })),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ content: discoveryDocument })),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${AUDIT_PATH}`, () =>
        HttpResponse.json({ content: AUDIT_CONTENT })),
      http.get(`${API_BASE_URL}/projects/ws-m/audit`, () =>
        HttpResponse.json([
          { index: 1, timestamp: "2026-07-25T00:00:01Z", user_input: "시작",
            ai_response: "Discovery 시작", context: "Session Start" },
          { index: 2, timestamp: "2026-07-25T00:00:02Z", user_input: "승인",
            ai_response: "승인 완료 — Discovery 단계를 종료합니다.", context: "최종 승인" },
        ])),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    // Scoped to the banner's role: "승인 완료" also appears in the audit
    // panel, so a bare text query is ambiguous.
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("승인 완료");
    // The re-approval condition is not "was a revision requested" but "did the document change" —
    // the judgment is a comparison with the hash at approval time (approval_store.py), and the
    // wording says so too.
    expect(banner).toHaveTextContent("문서가 바뀌면 다시 승인이 필요합니다");
    // The gate itself is gone — no further approval is pending.
    expect(screen.queryByRole("button", { name: /승인하고 다음 단계로/ })).not.toBeInTheDocument();
    // ...but a way back in remains.
    expect(screen.getAllByRole("link", { name: /수정 요청/ }).length).toBeGreaterThan(0);
  });

  it("keeps the gate visible when a revision followed the approval", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts`, () =>
        HttpResponse.json({ artifacts: [DISCOVERY_PATH, AUDIT_PATH] })),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ content: discoveryDocument })),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${AUDIT_PATH}`, () =>
        HttpResponse.json({ content: AUDIT_CONTENT })),
      http.get(`${API_BASE_URL}/projects/ws-m/audit`, () =>
        HttpResponse.json([
          { index: 2, timestamp: "2026-07-25T00:00:02Z", user_input: "승인",
            ai_response: "승인 완료", context: "최종 승인" },
          { index: 3, timestamp: "2026-07-25T00:00:03Z",
            user_input: "discovery-document.md 수정 요청: 3장 보강",
            ai_response: "문서를 수정했습니다", context: "수정 요청" },
        ])),
    );
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    expect(await screen.findByRole("button", { name: /승인하고 다음 단계로/ })).toBeInTheDocument();
  });
});

describe("Review page — document list auto-refresh", () => {
  const FAQ_PATH = "aiplc-docs/discovery/faq.md";

  it("polls the artifact list and shows a newly-created document without a manual reload", async () => {
    // Tree starts with only the discovery document; a background turn (from the
    // workspace) later creates faq.md. The poll must surface it.
    let artifacts = [DISCOVERY_PATH];
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts`, () =>
        HttpResponse.json({ artifacts }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ content: discoveryDocument }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${FAQ_PATH}`, () =>
        HttpResponse.json({ content: "# FAQ" }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/audit`, () => HttpResponse.json(auditEntries)),
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

  it("does not poll while the tab is hidden, and reloads at once when it comes back", async () => {
    let listCalls = 0;
    let artifacts = [DISCOVERY_PATH];
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m/artifacts`, () => {
        listCalls += 1;
        return HttpResponse.json({ artifacts });
      }),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${DISCOVERY_PATH}`, () =>
        HttpResponse.json({ content: discoveryDocument }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/files/${FAQ_PATH}`, () =>
        HttpResponse.json({ content: "# FAQ" }),
      ),
      http.get(`${API_BASE_URL}/projects/ws-m/audit`, () => HttpResponse.json(auditEntries)),
    );
    let visibility: DocumentVisibilityState = "visible";
    const spy = vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
    try {
      await act(async () => {
        render(<ReviewPage params={params} />);
      });
      expect(await screen.findByRole("button", { name: /discovery-document\.md/ })).toBeInTheDocument();

      visibility = "hidden";
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      const before = listCalls;
      artifacts = [DISCOVERY_PATH, FAQ_PATH];
      await act(async () => { await new Promise((r) => setTimeout(r, 5500)); });
      expect(listCalls).toBe(before);           // a full interval passed, no poll

      visibility = "visible";
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      // Well under the 5s interval: the return itself reloads.
      expect(
        await screen.findByRole("button", { name: /faq\.md/ }, { timeout: 1500 }),
      ).toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  }, 12000);
});
