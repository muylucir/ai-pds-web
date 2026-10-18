// frontend/lib/useProjectModel.test.tsx
import { describe, it, expect } from "vitest";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { useProjectMeta } from "./useProjectModel";

function Probe({ pid }: { pid?: string }) {
  const { modelLabel } = useProjectMeta(pid);
  return <span data-testid="label">{modelLabel ?? "(없음)"}</span>;
}

describe("useProjectMeta", () => {
  it("resolves the catalog name for the project's model", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1`, () => HttpResponse.json({
        project_id: "p1", name: null, created_at: null,
        model_id: "global.anthropic.claude-opus-5" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
        { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" }] })),
    );
    render(<Probe pid="p1" />);
    expect(await screen.findByText("Opus 5")).toBeInTheDocument();
  });

  it("shows the effort the project copied, not the catalog's current one", async () => {
    // A project copies the effort at creation time and uses it to the end — that value is what actually runs.
    server.use(
      http.get(`${API_BASE_URL}/projects/p1`, () => HttpResponse.json({
        project_id: "p1", name: null, created_at: null,
        model_id: "global.anthropic.claude-opus-5-5", effort: "medium" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
        { name: "Opus 5.5", model_id: "global.anthropic.claude-opus-5-5", effort: "high" }] })),
    );
    render(<Probe pid="p1" />);
    expect(await screen.findByText("Opus 5.5 · effort medium")).toBeInTheDocument();
  });

  it("falls back to the raw model id when the catalog no longer has it", async () => {
    // The consequence of the copy-the-value design must also show honestly on screen:
    // there can be projects running on a model the admin deleted from the catalog.
    server.use(
      http.get(`${API_BASE_URL}/projects/p1`, () => HttpResponse.json({
        project_id: "p1", name: null, created_at: null,
        model_id: "global.anthropic.claude-gone" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    render(<Probe pid="p1" />);
    expect(await screen.findByText("global.anthropic.claude-gone")).toBeInTheDocument();
  });

  it("is null when the project has no model", async () => {
    // The frontend cannot know what the server's env default is — showing nothing is better
    // than showing a guessed name.
    server.use(http.get(`${API_BASE_URL}/projects/p1`, () => HttpResponse.json({
      project_id: "p1", name: null, created_at: null, model_id: null })));
    render(<Probe pid="p1" />);
    expect(await screen.findByText("(없음)")).toBeInTheDocument();
  });

  it("is null when the project fetch fails", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1`, () =>
      HttpResponse.json({ detail: "boom" }, { status: 500 })));
    render(<Probe pid="p1" />);
    expect(await screen.findByText("(없음)")).toBeInTheDocument();
  });

  it("fetches nothing without a project id", () => {
    // onUnhandledRequest: "error", so sending a request makes this test fail.
    render(<Probe />);
    expect(screen.getByTestId("label")).toHaveTextContent("(없음)");
  });

  it("프로젝트의 생성물 언어를 함께 돌려준다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m`, () =>
        HttpResponse.json({ project_id: "ws-m", name: null, created_at: null,
                            model_id: null, language: "en" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    const { result } = renderHook(() => useProjectMeta("ws-m"));
    await waitFor(() => expect(result.current.language).toBe("en"));
  });

  it("언어를 모르는 응답(구 백엔드)에서는 null이다 — 배지를 그리지 않는다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m`, () =>
        HttpResponse.json({ project_id: "ws-m", name: null, created_at: null,
                            model_id: null })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    const { result } = renderHook(() => useProjectMeta("ws-m"));
    // Wait for the model lookup to finish, then check that the language is still null.
    await waitFor(() => expect(result.current.modelLabel).toBeNull());
    expect(result.current.language).toBeNull();
  });

  it("언어 자리에 임의 문자열이 오면 null이다 — 빈 배지를 그리지 않는다", async () => {
    // Without narrowing with isLocale, this value would go straight down to AppHeader,
    // and LANGUAGE_LABEL[that value] would be undefined, leaving only a badge with no text.
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m`, () =>
        HttpResponse.json({ project_id: "ws-m", name: null, created_at: null,
                            model_id: "m1", language: "klingon" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    const { result } = renderHook(() => useProjectMeta("ws-m"));
    // The model being filled in marks the point when the lookup finished.
    await waitFor(() => expect(result.current.modelLabel).toBe("m1"));
    expect(result.current.language).toBeNull();
  });

  it("projectId가 없으면 둘 다 null이다", () => {
    const { result } = renderHook(() => useProjectMeta(undefined));
    expect(result.current).toEqual({ modelLabel: null, language: null, access: null,
                                     readOnly: false });
  });

  it("열람 멤버면 readOnly다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ws-m`, () =>
        HttpResponse.json({ project_id: "ws-m", name: null, created_at: null,
                            model_id: null, owner: "kim", access: "member" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    const { result } = renderHook(() => useProjectMeta("ws-m"));
    await waitFor(() => expect(result.current.access).toBe("member"));
    expect(result.current.readOnly).toBe(true);
  });

  it("소유자·관리자, 그리고 권한을 모르는 응답(구 백엔드)은 readOnly가 아니다", async () => {
    for (const access of ["owner", "admin", undefined]) {
      server.use(
        http.get(`${API_BASE_URL}/projects/ws-m`, () =>
          HttpResponse.json({ project_id: "ws-m", name: null, created_at: null,
                              model_id: "m1", access })),
        http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
      );
      const { result, unmount } = renderHook(() => useProjectMeta("ws-m"));
      await waitFor(() => expect(result.current.modelLabel).toBe("m1"));
      expect(result.current.readOnly).toBe(false);
      unmount();
    }
  });
});
