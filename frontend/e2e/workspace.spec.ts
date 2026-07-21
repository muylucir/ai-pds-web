import { test, expect } from "@playwright/test";

// INTEGRATION: drives the unified 3-pane /workspace screen against a LIVE
// backend running the in-process Strands agent against real Bedrock (post
// MicroVM-removal — Task 11). Requires the backend to be up with
// PATHFINDER_S3_BUCKET + ANTHROPIC_MODEL + host AWS credentials configured;
// it is NOT part of the default CI unit suite and only runs in an environment
// that has those credentials.
//
// The old LocalSandbox scripted-demo flow (fixed demo questions, an echo AI
// message, a scripted answer-reflection message) is gone along with local
// mode itself. A real agent's response text is non-deterministic, so this
// spec asserts only the observable STRUCTURE of a turn: it starts, a user
// bubble appears with the sent text, and an AI bubble streams in (rendered
// through the shared Markdown component, which always wraps output in a
// `.prose` container) — content is not asserted. `timeout: 120_000` on the
// AI-bubble assertion accounts for a real Bedrock round-trip.
//
// The question-form interaction (radio selection -> submit -> reflected
// answer message) is deliberately NOT exercised here: nothing guarantees a
// real agent calls ask_questions on this particular turn, so it can't be
// asserted deterministically. Deterministic coverage of question
// rendering/submission lives in the component tests instead — see
// QuestionCard.test.tsx and QuestionForm.test.tsx.
test("웰컴 카드에서 턴을 시작하면 AI 응답이 스트리밍된다", async ({ page }) => {
  const pid = `e2e-workspace-${Date.now()}`;
  await page.goto("/");
  await page.getByLabel("프로젝트 ID").fill(pid);
  await page.getByRole("button", { name: "프로젝트 생성" }).click();
  await expect(page.getByRole("link", { name: new RegExp(pid) }).first()).toBeVisible();

  await page.goto(`/projects/${pid}/workspace`);
  await expect(page.getByLabel("채팅 메시지 입력")).toBeVisible();
  await expect(page.getByText("어떻게 시작할까요?")).toBeVisible();

  const rightPanel = page.getByLabel("컨텍스트 패널");
  await expect(rightPanel).toBeVisible();

  // Kick off the first turn via the welcome card's Path A button.
  await page.getByRole("button", { name: /Path A/ }).click();
  await expect(page.getByText("어떻게 시작할까요?")).toHaveCount(0);

  const timeline = page.getByLabel("대화 타임라인");
  // The user bubble (the literal Path A starter text) appears.
  await expect(timeline.getByText(/Path A/).first()).toBeVisible();
  // The real agent's AI bubble streams in — content is unasserted, only the
  // Markdown wrapper's presence is (`.prose`, from components/Markdown.tsx).
  await expect(timeline.locator(".prose").first()).toBeVisible({ timeout: 120_000 });

  // No turn-failure banner appeared.
  await expect(page.getByText(/연결이 끊어졌습니다/)).toHaveCount(0);
});

test("retired /questions and /canvas tabs redirect to /workspace", async ({ page }) => {
  const pid = `e2e-redirect-${Date.now()}`;
  const create = await page.request.post("/api/projects", { data: { project_id: pid } });
  expect(create.ok()).toBe(true);

  await page.goto(`/projects/${pid}/questions`);
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/workspace$`));

  await page.goto(`/projects/${pid}/canvas`);
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/workspace$`));
});
