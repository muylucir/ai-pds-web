import { test, expect } from "@playwright/test";

// INTEGRATION: requires a live backend with a project that has a question file
// (seed via the backend before running). Answers Q1 and submits.
test("answer a question and submit", async ({ page }) => {
  const pid = process.env.E2E_PROJECT_ID ?? "pilot1";
  await page.goto(`/projects/${pid}/questions`);
  await expect(page.getByRole("button", { name: /답변 제출/ })).toBeVisible();
  await page.getByRole("button", { name: /답변 제출/ }).click();
  // After submit the form re-renders (reparsed file) without an error banner.
  await expect(page.getByText(/제출에 실패/)).toHaveCount(0);
});
