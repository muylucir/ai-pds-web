// frontend/components/CreateProjectForm.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { CreateProjectForm } from "./CreateProjectForm";

describe("CreateProjectForm", () => {
  it("submits project_id + name and calls onCreated", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    let body: any;
    server.use(
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: body.project_id, name: body.name ?? null });
      }),
    );
    render(<CreateProjectForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "pilot2");
    await user.type(screen.getByLabelText("프로젝트 이름 (선택)"), "신규 세션");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body).toEqual({ project_id: "pilot2", name: "신규 세션" });
    expect(onCreated).toHaveBeenCalledWith({ project_id: "pilot2", name: "신규 세션" });
  });

  it("shows a Korean conflict message on 409", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ detail: "project exists" }, { status: 409 }),
      ),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "dup");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(await screen.findByText("이미 존재하는 프로젝트 ID입니다.")).toBeInTheDocument();
  });
});
