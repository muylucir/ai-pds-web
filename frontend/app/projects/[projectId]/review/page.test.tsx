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

function mockDocAndAudit() {
  server.use(
    http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    http.get(`${API_BASE_URL}/projects/pilot1/audit`, () => HttpResponse.json(auditEntries)),
  );
}

describe("Review page", () => {
  it("renders the document and the approval gate", async () => {
    mockDocAndAudit();
    // `use(params)` suspends on the first render because the test's plain
    // Promise.resolve(...) params (unlike Next's internally-tracked params
    // thenable) isn't pre-marked as settled. Wrapping the initial render in
    // act() lets that Suspense retry flush before we start querying/waiting.
    await act(async () => {
      render(<ReviewPage params={params} />);
    });
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /승인하고 다음 단계로/ })).toBeInTheDocument();
  });

  it("clicking Approve POSTs {text:'승인'} to /message", async () => {
    mockDocAndAudit();
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
    mockDocAndAudit();
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
});
