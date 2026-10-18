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
    // A real network drop — the situation where the hook's existing "연결이 끊어졌습니다" (connection
    // lost) message is right.
    mockMe({ authenticated: true, email: "a@example.com", role: "pm" }, 200);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate when the check itself fails", async () => {
    // Sending the user to login when even /api/auth/me cannot be reached would kick the user
    // out of the screen they are working on when the backend merely died for a moment.
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
    // A 401 alone is not taken as proof: if the body does not parse (e.g., a 401 produced by some
    // other layer cutting in), it is treated as "check failed" and the user is not kicked out.
    server.use(http.get("*/api/auth/me", () => new HttpResponse("not json", { status: 401 })));
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  // Even with a 401 and a successfully parsed body, expiry is not asserted unless
  // authenticated is exactly false — this boundary is pinned directly so that a future
  // refactor, such as flipping `=== false` to `!== true`, cannot silently bring back
  // over-eager eviction.
  it("does not navigate on a 401 body with authenticated:true — status alone is not the verdict", async () => {
    mockMe({ authenticated: true }, 401);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate on a 401 body missing the authenticated key", async () => {
    mockMe({}, 401);
    const navigate = vi.fn();
    await expect(redirectIfSessionExpired(navigate)).resolves.toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not navigate on a 401 body with authenticated as a non-boolean string", async () => {
    mockMe({ authenticated: "false" }, 401);
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
