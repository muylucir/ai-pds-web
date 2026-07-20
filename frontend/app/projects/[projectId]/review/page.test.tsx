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
