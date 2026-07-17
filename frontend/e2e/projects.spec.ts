import { test, expect } from "@playwright/test";

// INTEGRATION: drives the real UI against a real backend. Creates a uniquely
// named project and verifies it appears in the list.
test("create a project and see it in the list", async ({ page }) => {
  const pid = `e2e-${Date.now()}`;
  await page.goto("/");
  await page.getByLabel("프로젝트 ID").fill(pid);
  await page.getByLabel("프로젝트 이름 (선택)").fill("E2E 세션");
  await page.getByRole("button", { name: "프로젝트 생성" }).click();
  await expect(page.getByText("E2E 세션")).toBeVisible();
});
