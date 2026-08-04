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

  it("falls back to the raw model id when the catalog no longer has it", async () => {
    // 값을 복사해 두는 설계의 결과가 화면에서도 정직하게 드러나야 한다:
    // 관리자가 카탈로그에서 지운 모델로 도는 프로젝트가 있을 수 있다.
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
    // 서버의 env 기본값이 무엇인지 프론트는 알 수 없다 — 추측한 이름을
    // 보여주는 것보다 아무것도 안 보여주는 게 낫다.
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
    // onUnhandledRequest: "error"이므로, 요청을 보내면 이 테스트가 실패한다.
    render(<Probe />);
    expect(screen.getByTestId("label")).toHaveTextContent("(없음)");
  });

  it("프로젝트의 생성물 언어를 함께 돌려준다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1`, () =>
        HttpResponse.json({ project_id: "pilot1", name: null, created_at: null,
                            model_id: null, language: "en" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    const { result } = renderHook(() => useProjectMeta("pilot1"));
    await waitFor(() => expect(result.current.language).toBe("en"));
  });

  it("언어를 모르는 응답(구 백엔드)에서는 null이다 — 배지를 그리지 않는다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1`, () =>
        HttpResponse.json({ project_id: "pilot1", name: null, created_at: null,
                            model_id: null })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    const { result } = renderHook(() => useProjectMeta("pilot1"));
    // 모델 조회가 끝나기를 기다린 뒤 언어가 여전히 null인지 본다.
    await waitFor(() => expect(result.current.modelLabel).toBeNull());
    expect(result.current.language).toBeNull();
  });

  it("언어 자리에 임의 문자열이 오면 null이다 — 빈 배지를 그리지 않는다", async () => {
    // isLocale로 좁히지 않으면 이 값이 그대로 AppHeader에 내려가고,
    // LANGUAGE_LABEL[그 값]이 undefined가 되어 글자 없는 배지만 남는다.
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1`, () =>
        HttpResponse.json({ project_id: "pilot1", name: null, created_at: null,
                            model_id: "m1", language: "klingon" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    const { result } = renderHook(() => useProjectMeta("pilot1"));
    // 모델이 채워지는 것으로 조회가 끝난 시점을 잡는다.
    await waitFor(() => expect(result.current.modelLabel).toBe("m1"));
    expect(result.current.language).toBeNull();
  });

  it("projectId가 없으면 둘 다 null이다", () => {
    const { result } = renderHook(() => useProjectMeta(undefined));
    expect(result.current).toEqual({ modelLabel: null, language: null });
  });
});
