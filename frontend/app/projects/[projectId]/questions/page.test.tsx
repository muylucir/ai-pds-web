import { describe, it, expect, vi } from "vitest";
import QuestionsPage from "./page";

// /questions is retired (Task 11) — it now server-redirects into the unified
// /workspace screen (whose right panel shows QuestionForm when a question
// interrupt is pending). next/navigation's redirect() isn't wired in the
// unit test renderer, so it's mocked as a spy.
const redirectSpy = vi.fn();
vi.mock("next/navigation", () => ({
  redirect: (path: string) => redirectSpy(path),
}));

describe("Questions page (retired route)", () => {
  it("redirects to the project's workspace screen", async () => {
    await QuestionsPage({ params: Promise.resolve({ projectId: "pilot1" }) });
    expect(redirectSpy).toHaveBeenCalledWith("/projects/pilot1/workspace");
  });
});
