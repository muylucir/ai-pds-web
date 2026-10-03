// frontend/components/admin/AddModelModal.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { AddModelModal } from "./AddModelModal";

describe("AddModelModal", () => {
  it("posts the name, id and display flag then closes", async () => {
    const user = userEvent.setup();
    const onAdded = vi.fn();
    const onClose = vi.fn();
    let body: any;
    server.use(http.post(`${API_BASE_URL}/admin/models`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...body }, { status: 201 });
    }));
    render(<AddModelModal onAdded={onAdded} onClose={onClose} />);
    await user.type(screen.getByLabelText("표시 이름"), "Opus 4.8");
    await user.type(screen.getByLabelText("모델 ID"), "global.anthropic.claude-opus-4-8");
    await user.click(screen.getByRole("button", { name: "추가" }));
    // effort를 고르지 않으면 null — CLI 기본값이다(모델마다 달라 프론트가 정하지 않는다).
    expect(body).toEqual({ name: "Opus 4.8",
                           model_id: "global.anthropic.claude-opus-4-8",
                           display: true, effort: null });
    expect(onAdded).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("sends the chosen effort", async () => {
    const user = userEvent.setup();
    let body: any;
    server.use(http.post(`${API_BASE_URL}/admin/models`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...body }, { status: 201 });
    }));
    render(<AddModelModal onAdded={vi.fn()} onClose={vi.fn()} />);
    await user.type(screen.getByLabelText("표시 이름"), "Sonnet 5.5");
    await user.type(screen.getByLabelText("모델 ID"), "global.anthropic.claude-sonnet-5-5");
    await user.selectOptions(screen.getByLabelText("effort"), "high");
    await user.click(screen.getByRole("button", { name: "추가" }));
    expect(body.effort).toBe("high");
  });

  it("can add a hidden model", async () => {
    const user = userEvent.setup();
    let body: any;
    server.use(http.post(`${API_BASE_URL}/admin/models`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...body }, { status: 201 });
    }));
    render(<AddModelModal onAdded={vi.fn()} onClose={vi.fn()} />);
    await user.type(screen.getByLabelText("표시 이름"), "숨김");
    await user.type(screen.getByLabelText("모델 ID"), "global.anthropic.claude-opus-4-7");
    await user.click(screen.getByLabelText("콤보박스에 표시"));
    await user.click(screen.getByRole("button", { name: "추가" }));
    expect(body.display).toBe(false);
  });

  it("keeps the modal open and shows the detail on 409", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    server.use(http.post(`${API_BASE_URL}/admin/models`, () =>
      HttpResponse.json({ detail: "이미 등록된 모델입니다." }, { status: 409 })));
    render(<AddModelModal onAdded={vi.fn()} onClose={onClose} />);
    await user.type(screen.getByLabelText("표시 이름"), "중복");
    await user.type(screen.getByLabelText("모델 ID"), "global.anthropic.claude-opus-5");
    await user.click(screen.getByRole("button", { name: "추가" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("이미 등록된 모델입니다.");
    expect(onClose).not.toHaveBeenCalled();
  });
});
