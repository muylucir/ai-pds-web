// frontend/components/CreateProjectForm.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { CreateProjectForm } from "./CreateProjectForm";

describe("CreateProjectForm", () => {
  it("submits project_id + name + the selected model", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    let body: any;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
        { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" },
        { name: "Sonnet 5", model_id: "global.anthropic.claude-sonnet-5" },
      ] })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: body.project_id, name: body.name ?? null,
                                   model_id: body.model_id ?? null });
      }),
    );
    render(<CreateProjectForm onCreated={onCreated} />);
    // 목록이 도착할 때까지 기다린다 — 첫 항목이 기본 선택된다.
    await screen.findByRole("option", { name: "Opus 5" });
    await user.type(screen.getByLabelText("프로젝트 ID"), "pilot2");
    await user.type(screen.getByLabelText("프로젝트 이름 (선택)"), "신규 세션");
    await user.selectOptions(screen.getByLabelText("AI 모델"), "global.anthropic.claude-sonnet-5");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body).toEqual({ project_id: "pilot2", name: "신규 세션",
                           model_id: "global.anthropic.claude-sonnet-5" });
    // onCreated가 서버 응답(생성된 프로젝트)으로 호출됐는지 확인한다 — 이
    // 핸들러는 {project_id, name, model_id}를 돌려준다.
    expect(onCreated).toHaveBeenCalledWith({ project_id: "pilot2", name: "신규 세션",
                                             model_id: "global.anthropic.claude-sonnet-5" });
  });

  it("shows model names only — never the raw model id", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
      { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" },
    ] })));
    render(<CreateProjectForm onCreated={vi.fn()} />);
    const option = await screen.findByRole("option", { name: "Opus 5" });
    // 요구사항: "콤보박스에는 모델 이름만 표시". id는 value에만 있다.
    expect(option.textContent).toBe("Opus 5");
    expect(screen.queryByText(/global\.anthropic/)).toBeNull();
  });

  it("defaults to the first model in the list", async () => {
    const user = userEvent.setup();
    let body: any;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
        { name: "Opus 5", model_id: "m-first" },
        { name: "Sonnet 5", model_id: "m-second" },
      ] })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: "p", name: null, model_id: body.model_id });
      }),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await screen.findByRole("option", { name: "Opus 5" });
    await user.type(screen.getByLabelText("프로젝트 ID"), "p");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body.model_id).toBe("m-first");
  });

  it("still creates a project when the model list fails to load", async () => {
    const user = userEvent.setup();
    let body: any;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ detail: "boom" },
                                                                 { status: 500 })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: "p", name: null, model_id: null });
      }),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "p");
    // 셀렉트는 비활성이지만 생성은 막지 않는다 — 카탈로그 조회 실패가
    // 프로젝트 생성 전체를 막는 것은 과하다(서버가 env 기본값으로 떨어진다).
    expect(await screen.findByLabelText("AI 모델")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body).toEqual({ project_id: "p" });
  });

  it("disables the select while the list is empty", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })));
    render(<CreateProjectForm onCreated={vi.fn()} />);
    expect(await screen.findByLabelText("AI 모델")).toBeDisabled();
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
