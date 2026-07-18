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
  // agent turn over GET /events). We scope assertions to the chat timeline so
  // this doesn't false-positive against the AppHeader's "AI" logo chip, which
  // is visible before any turn.
  const timeline = page.getByLabel("대화 타임라인");
  await expect(timeline.getByText("프로토타입에 대해 알려줘")).toBeVisible();
  await expect(timeline.locator("text=AI").first()).toBeVisible({ timeout: 30_000 });
});

// C2 INTEGRATION: a turn that touches discovery-document.md materializes an
// artifact card; clicking it switches the right panel to the 문서 tab and
// shows the Living Document + approval controls. Requires a seeded project
// whose next agent turn is expected to write/update discovery-document.md
// (E2E_PROJECT_ID should point at a fixture project pre-staged for this, e.g.
// one already past Envision so "문서 갱신해줘" triggers a document rewrite).
test("an artifact card opens the right panel's 문서 tab with the Living Document", async ({ page }) => {
  const pid = process.env.E2E_PROJECT_ID ?? "pilot1";
  await page.goto(`/projects/${pid}/canvas`);
  await page.getByLabel("채팅 메시지 입력").fill("Discovery Document를 최신 내용으로 갱신해줘");
  await page.getByRole("button", { name: "전송" }).click();

  const artifactButton = page.getByRole("button", { name: /우측 패널에서 열기/ });
  await expect(artifactButton).toBeVisible({ timeout: 30_000 });
  await artifactButton.click();

  await expect(page.getByRole("tab", { name: "문서" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "✓ 이 문서 승인" })).toBeVisible();
});
