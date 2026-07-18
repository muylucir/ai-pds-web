import { test, expect } from "@playwright/test";

// INTEGRATION (AWS/backend-required): drives the canvas against a real backend
// + a seeded project. Sends a message and expects a streamed AI response to
// appear in the timeline. Excluded from the unit (vitest) path.
test("send a chat message and see a streamed AI reply", async ({ page }) => {
  const pid = process.env.E2E_PROJECT_ID ?? "pilot1";
  await page.goto(`/projects/${pid}/canvas`);
  await expect(page.getByLabel("채팅 메시지 입력")).toBeVisible();
  await page.getByLabel("채팅 메시지 입력").fill("프로토타입에 대해 알려줘");
  await page.getByRole("button", { name: "전송" }).click();
  // The user bubble appears; the AI bubble streams in (the backend relays the
  // agent turn over GET /events). We assert an AI avatar bubble materializes.
  await expect(page.getByText("프로토타입에 대해 알려줘")).toBeVisible();
  await expect(page.locator("text=AI").first()).toBeVisible();
});
