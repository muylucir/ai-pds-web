// frontend/components/CreateProjectForm.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
    // Wait until the list arrives — the first item is selected by default.
    await screen.findByRole("option", { name: "Opus 5" });
    await user.type(screen.getByLabelText("프로젝트 ID"), "pilot2");
    await user.type(screen.getByLabelText("프로젝트 이름 (선택)"), "신규 세션");
    await user.selectOptions(screen.getByLabelText("AI 모델"), "global.anthropic.claude-sonnet-5");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    // language is always sent even if the select is untouched — the default is ko.
    expect(body).toEqual({ project_id: "pilot2", name: "신규 세션",
                           model_id: "global.anthropic.claude-sonnet-5",
                           language: "ko" });
    // Check that onCreated was called with the server response (the created project) — this
    // handler returns {project_id, name, model_id}.
    expect(onCreated).toHaveBeenCalledWith({ project_id: "pilot2", name: "신규 세션",
                                             model_id: "global.anthropic.claude-sonnet-5" });
  });

  it("shows model names only — never the raw model id", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
      { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" },
    ] })));
    render(<CreateProjectForm onCreated={vi.fn()} />);
    const option = await screen.findByRole("option", { name: "Opus 5" });
    // Requirement: "콤보박스에는 모델 이름만 표시" (show only the model name in the combo box). The id lives only in value.
    expect(option.textContent).toBe("Opus 5");
    expect(screen.queryByText(/global\.anthropic/)).toBeNull();
  });

  it("shows each model's effort next to its name", async () => {
    // Even for the same model, speed varies a lot with effort — the person choosing needs to know.
    // An item without effort (CLI default) shows only the name.
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
      { name: "Opus 5.5", model_id: "global.anthropic.claude-opus-5-5", effort: "medium" },
      { name: "Opus 4.6", model_id: "global.anthropic.claude-opus-4-6-v1", effort: null },
    ] })));
    render(<CreateProjectForm onCreated={vi.fn()} />);
    expect(await screen.findByRole("option", { name: "Opus 5.5 · effort medium" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Opus 4.6" })).toBeInTheDocument();
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
    // The select is disabled, but creation is not blocked — letting a failed catalog lookup
    // block project creation entirely would be excessive (the server falls back to the env default).
    expect(await screen.findByLabelText("AI 모델")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body).toEqual({ project_id: "p", language: "ko" });
  });

  it("disables the select while the list is empty", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })));
    render(<CreateProjectForm onCreated={vi.fn()} />);
    expect(await screen.findByLabelText("AI 모델")).toBeDisabled();
  });

  it("프로젝트 ID 입력창은 영문·숫자·-·_ 만 받는다", async () => {
    const user = userEvent.setup();
    let body: unknown = null;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: "my-pilot_2", name: null, model_id: null,
                                   language: "ko" });
      }),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    const input = screen.getByLabelText("프로젝트 ID");
    // Spaces, Hangul, slashes, and dots do not get through; only allowed characters remain.
    await user.type(input, "my pilot_2 한글/x.y");
    expect(input).toHaveValue("mypilot_2xy");
    // Tell the user that characters were filtered out — silently dropping them looks like swallowed keystrokes.
    expect(screen.getByText(
      "영문·숫자·하이픈(-)·밑줄(_)만 쓸 수 있습니다. 공백은 안 됩니다.",
    )).toBeInTheDocument();
    // An id made only of allowed characters is sent as is.
    await user.clear(input);
    expect(screen.queryByText(/영문·숫자·하이픈/)).toBeNull();
    await user.type(input, "my-pilot_2");
    expect(input).toHaveValue("my-pilot_2");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    await waitFor(() => expect(body).toMatchObject({ project_id: "my-pilot_2" }));
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

  it("고른 언어를 생성 요청에 실어 보낸다", async () => {
    const user = userEvent.setup();
    let body: unknown = null;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: "p1", name: null, model_id: null,
                                   language: "en" });
      }),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "p1");
    await user.selectOptions(screen.getByLabelText("문서 언어"), "en");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    await waitFor(() => expect(body).toMatchObject({ project_id: "p1", language: "en" }));
  });

  it("언어를 고르지 않으면 ko로 보낸다", async () => {
    // The default is ko, not the UI locale — someone using the English UI creating a Korean
    // project is normal, and we do not make that choice for them.
    const user = userEvent.setup();
    let body: unknown = null;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: "p2", name: null, model_id: null,
                                   language: "ko" });
      }),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "p2");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    await waitFor(() => expect(body).toMatchObject({ language: "ko" }));
  });
});
