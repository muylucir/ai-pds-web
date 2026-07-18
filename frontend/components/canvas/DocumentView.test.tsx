import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { DocumentView } from "./DocumentView";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";

describe("DocumentView", () => {
  it("renders the mockup's document header and the fetched markdown", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={vi.fn()} busy={false} />);
    });
    expect(screen.getByText("discovery-document.md")).toBeInTheDocument();
    expect(screen.getByText("Living")).toBeInTheDocument();
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
  });

  it('shows "문서가 아직 없습니다." on a 404 (no document yet)', async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () =>
        HttpResponse.json({ detail: "none" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText("문서가 아직 없습니다.")).toBeInTheDocument();
  });

  it("shows a Korean load-error line on a non-404 error", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText(/문서를 불러오지 못했습니다/)).toBeInTheDocument();
  });

  it("clicking 이 문서 승인 calls onApprove", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    const onApprove = vi.fn();
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={onApprove} onRevise={vi.fn()} busy={false} />);
    });
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: "✓ 이 문서 승인" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("submitting a revision calls onRevise with the typed text", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    const onRevise = vi.fn();
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={onRevise} busy={false} />);
    });
    await screen.findByText("Press Release");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "✏️ 수정 요청" }));
    await user.type(screen.getByLabelText("수정 요청 사항"), "FAQ에 다국어 지원 추가");
    await user.click(screen.getByRole("button", { name: "수정 요청 제출" }));
    expect(onRevise).toHaveBeenCalledWith("FAQ에 다국어 지원 추가");
  });
});
