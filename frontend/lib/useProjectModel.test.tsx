// frontend/lib/useProjectModel.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { useProjectModel } from "./useProjectModel";

function Probe({ pid }: { pid?: string }) {
  const label = useProjectModel(pid);
  return <span data-testid="label">{label ?? "(없음)"}</span>;
}

describe("useProjectModel", () => {
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
});
