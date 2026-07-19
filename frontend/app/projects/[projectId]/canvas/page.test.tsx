import { describe, it, expect, vi } from "vitest";
import CanvasPage from "./page";

// /canvas is retired (Task 11) — it now server-redirects into the unified
// /workspace screen. next/navigation's redirect() isn't wired in the unit
// test renderer, so it's mocked as a spy (same pattern the questions page
// test used for useSearchParams/useRouter).
const redirectSpy = vi.fn();
vi.mock("next/navigation", () => ({
  redirect: (path: string) => redirectSpy(path),
}));

describe("Canvas page (retired route)", () => {
  it("redirects to the project's workspace screen", async () => {
    await CanvasPage({ params: Promise.resolve({ projectId: "pilot1" }) });
    expect(redirectSpy).toHaveBeenCalledWith("/projects/pilot1/workspace");
  });
});
