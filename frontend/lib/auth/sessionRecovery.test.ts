import { http, HttpResponse, type JsonBodyType } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/msw/server";
import { redirectIfSessionExpired } from "./sessionRecovery";

function mockMe(body: JsonBodyType, status: number) {
  server.use(http.get("*/api/auth/me", () => HttpResponse.json(body, { status })));
}

describe("redirectIfSessionExpired", () => {
  it("navigates to /login when the session is gone", async () => {
    mockMe({ authenticated: false }, 401);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(true);
    expect(navigate).toHaveBeenCalledWith("/login");
  });

  it("does nothing when the session is still valid", async () => {
    // 진짜 네트워크 끊김 — 훅의 기존 "연결이 끊어졌습니다" 메시지가 맞는 상황이다.
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" }, 200);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate when the check itself fails", async () => {
    // /api/auth/me까지 닿지 않는 상황에서 로그인으로 보내면, 백엔드가 잠깐
    // 죽은 것뿐인데 사용자를 작업 중인 화면에서 쫓아낸다.
    server.use(http.get("*/api/auth/me", () => HttpResponse.error()));
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate on a 500 — the backend may just be restarting", async () => {
    mockMe({ error: "internal" }, 500);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate on a 502 — same restarting-backend case as a 500", async () => {
    mockMe({ error: "bad gateway" }, 502);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate when a 200 body fails to parse — still alive by status", async () => {
    server.use(http.get("*/api/auth/me", () => new HttpResponse("not json", { status: 200 })));
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate when a 401 body is malformed — an indeterminate check is not a verdict", async () => {
    // 401 자체만으로 단정하지 않는다: 본문이 파싱되지 않으면(예: 다른 계층이
    // 끼어들어 만든 401) "확인 실패"로 취급해 사용자를 쫓아내지 않는다.
    server.use(http.get("*/api/auth/me", () => new HttpResponse("not json", { status: 401 })));
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("preserves the current path so login can return there", async () => {
    mockMe({ authenticated: false }, 401);
    const navigate = vi.fn();
    await redirectIfSessionExpired(navigate, "/projects/p1/canvas");
    expect(navigate).toHaveBeenCalledWith(
      "/login?next=%2Fprojects%2Fp1%2Fcanvas");
  });
});
