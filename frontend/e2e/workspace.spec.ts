import { test, expect } from "@playwright/test";

// INTEGRATION: drives the unified 3-pane /workspace screen (Task 11) against a
// live backend running the LocalSandbox demo scenario (Task 8's structured
// first-turn script). Replaces wizard.spec.ts + canvas.spec.ts, which drove
// the now-retired standalone /questions and /canvas tabs.
//
// Flow: create a project -> /workspace -> send "시작" -> the demo script's
// first turn emits message + stage(Envision, in_progress) + questions (2 demo
// Qs) -> the right panel renders QuestionForm -> pick a radio for each
// question -> "답변 제출" -> send_answers's scripted round emits message +
// stage(Envision, completed) + document -> chat reflects the answer message,
// the left sidebar shows Envision completed, and no error appears. Finally,
// the retired /questions and /canvas routes redirect back to /workspace.
test("시작 -> 데모 질문 응답 -> Envision 완료 + 문서 이벤트", async ({ page }) => {
  const pid = `e2e-workspace-${Date.now()}`;

  await page.goto("/");
  await page.getByLabel("프로젝트 ID").fill(pid);
  await page.getByRole("button", { name: "프로젝트 생성" }).click();
  await expect(page.getByRole("link", { name: new RegExp(pid) }).first()).toBeVisible();

  await page.goto(`/projects/${pid}/workspace`);
  await expect(page.getByLabel("채팅 메시지 입력")).toBeVisible();

  // Kick off the demo scenario's first turn.
  await page.getByLabel("채팅 메시지 입력").fill("시작");
  await page.getByRole("button", { name: "전송" }).click();

  const timeline = page.getByLabel("대화 타임라인");
  await expect(timeline.getByText("시작")).toBeVisible();

  // The right panel (컨텍스트 패널) switches to the QuestionForm mode once the
  // "questions" event lands — the demo scenario's 2 fixed questions.
  const rightPanel = page.getByLabel("컨텍스트 패널");
  await expect(rightPanel.getByText("주요 사용자는 누구인가요?")).toBeVisible({ timeout: 30_000 });
  await expect(rightPanel.getByText("가장 큰 페인포인트는?")).toBeVisible();

  // Select the AI-recommended option for each of the 2 demo questions
  // (radios are visually hidden — `sr-only peer` — so the visible label text
  // is the interaction target, matching QuestionCard.tsx's <label> wrapping).
  await rightPanel.getByText("사내 PM").click();
  await rightPanel.getByText("도구 접근성").click();

  await rightPanel.getByRole("button", { name: /답변 제출/ }).click();

  // send_answers's scripted round reflects the answers in a new AI message
  // and completes the Envision stage.
  await expect(timeline.getByText(/답변\(.*\)을 반영했습니다/)).toBeVisible({ timeout: 30_000 });
  const sidebar = page.getByLabel("스테이지 진행 상황");
  await expect(sidebar.getByText("Envision")).toBeVisible();

  // The right panel drops out of "questions" mode (pendingQuestions cleared)
  // once answers are submitted — the question text disappears.
  await expect(rightPanel.getByText("주요 사용자는 누구인가요?")).toHaveCount(0);

  // No unhandled turn error surfaced in the AI bubble.
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
