// frontend/components/admin/ModelTable.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ModelTable } from "./ModelTable";
import type { AdminModel } from "@/lib/api/models";

const MODELS: AdminModel[] = [
  { name: "Opus 5", model_id: "global.anthropic.claude-opus-5", display: true },
  { name: "Opus 4.8", model_id: "global.anthropic.claude-opus-4-8", display: false },
];

describe("ModelTable", () => {
  it("shows the name, the model id and the display state", () => {
    render(<ModelTable models={MODELS} onChanged={vi.fn()} />);
    expect(screen.getByText("Opus 5")).toBeInTheDocument();
    // 관리자 화면은 id를 보여준다 — 무엇을 등록했는지 확인해야 한다
    // (콤보박스와 다른 점이다).
    expect(screen.getByText("global.anthropic.claude-opus-5")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Opus 5 표시" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Opus 4.8 표시" })).not.toBeChecked();
  });

  it("toggling display patches the model and reloads", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    let body: any;
    server.use(http.patch(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-5`,
      async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...MODELS[0], display: false });
      }));
    render(<ModelTable models={MODELS} onChanged={onChanged} />);
    await user.click(screen.getByRole("switch", { name: "Opus 5 표시" }));
    expect(body).toEqual({ display: false });
    expect(onChanged).toHaveBeenCalled();
  });

  it("shows the server's message when a sixth display is rejected", async () => {
    const user = userEvent.setup();
    server.use(http.patch(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-4-8`,
      () => HttpResponse.json({ detail: "at most 5 models can be displayed" },
                              { status: 400 })));
    render(<ModelTable models={MODELS} onChanged={vi.fn()} />);
    await user.click(screen.getByRole("switch", { name: "Opus 4.8 표시" }));
    // 프론트가 규칙을 복제하지 않는다 — 서버 문장을 그대로 보여준다.
    expect(await screen.findByRole("alert"))
      .toHaveTextContent("at most 5 models can be displayed");
  });

  it("deletes after a confirmation", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    let deleted = false;
    server.use(http.delete(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-5`,
      () => { deleted = true; return new HttpResponse(null, { status: 204 }); }));
    render(<ModelTable models={MODELS} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: "Opus 5 삭제" }));
    await user.click(await screen.findByRole("button", { name: "삭제 확인" }));
    expect(deleted).toBe(true);
    expect(onChanged).toHaveBeenCalled();
  });

  it("does not delete when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    let deleted = false;
    server.use(http.delete(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-5`,
      () => { deleted = true; return new HttpResponse(null, { status: 204 }); }));
    render(<ModelTable models={MODELS} onChanged={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Opus 5 삭제" }));
    await user.click(await screen.findByRole("button", { name: "취소" }));
    expect(deleted).toBe(false);
  });
});
