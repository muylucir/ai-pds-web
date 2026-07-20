import { test, expect } from "@playwright/test";

// INTEGRATION: drives the unified 3-pane /workspace screen (Task 11) against a
// live backend running the LocalSandbox demo scenario (Task 8's structured
// first-turn script). Replaces wizard.spec.ts + canvas.spec.ts, which drove
// the now-retired standalone /questions and /canvas tabs.
//
// Flow: create a project -> /workspace -> the welcome card (Task 6) greets a
// history-empty/non-streaming/no-pending-questions project -> click "Path A"
// (WelcomeCard.tsx's onStart sends the fixed Path-A starter message) -> the
// demo script's first turn emits message + stage(Envision, in_progress) +
// questions (2 demo Qs, both radio-only — LocalSandbox's fixed demo payload
// has no multi_select question, so that widget is covered by
// QuestionCard.test.tsx instead) -> the right panel renders QuestionForm and
// the welcome card is gone -> pick a radio for each question -> "답변 제출"
// -> send_answers's scripted round emits message + stage(Envision,
// completed) + document -> chat reflects the answer message (rendered
// through the shared Markdown component — Task 4/9), the left sidebar shows
// Envision completed, and no error appears. Finally, the retired /questions
// and /canvas routes redirect back to /workspace.
test("웰컴 카드 시작 -> 데모 질문 응답 -> Envision 완료 + 문서 이벤트", async ({ page }) => {
  const pid = `e2e-workspace-${Date.now()}`;

  await page.goto("/");
  await page.getByLabel("프로젝트 ID").fill(pid);
  await page.getByRole("button", { name: "프로젝트 생성" }).click();
  await expect(page.getByRole("link", { name: new RegExp(pid) }).first()).toBeVisible();

  await page.goto(`/projects/${pid}/workspace`);
  await expect(page.getByLabel("채팅 메시지 입력")).toBeVisible();

  // A brand-new project has no S3 session, so GET /history resolves to []
  // (local mode) — historyLoading flips false with an empty timeline, no
  // pending interrupt, and no in-flight turn, so WorkspacePage's
  // `showWelcome` gate renders the WelcomeCard instead of ChatTimeline.
  await expect(page.getByText("어떻게 시작할까요?")).toBeVisible();

  // Scroll containers (spec §7): the chat area and the context panel are
  // each independently scrollable — assert both exist as distinct
  // overflow-y-auto regions rather than one shared scroller. The welcome
  // card sits inside WorkspacePage's own `overflow-y-auto` wrapper (the
  // ChatTimeline scroller only mounts once the card is replaced by the
  // timeline), and the right panel (컨텍스트 패널) is present from the start.
  await expect(page.locator(".overflow-y-auto").first()).toBeVisible();
  const rightPanel = page.getByLabel("컨텍스트 패널");
  await expect(rightPanel).toBeVisible();

  // Kick off the demo scenario's first turn via the welcome card's Path A
  // button instead of typing into the chat input.
  await page.getByRole("button", { name: /Path A/ }).click();

  // The welcome card is replaced by the chat timeline once the turn starts
  // (items.length > 0 flips `showWelcome` false).
  await expect(page.getByText("어떻게 시작할까요?")).toHaveCount(0);

  const timeline = page.getByLabel("대화 타임라인");
  // Two elements match /Path A/ here: the user bubble (the literal starter
  // text) AND LocalSandbox's first AI message, which echoes the input
  // verbatim ("'<text>' 요청을 받았습니다...") — `.first()` disambiguates the
  // strict-mode locator instead of asserting on a specific role.
  await expect(timeline.getByText(/Path A/).first()).toBeVisible();
  // The chat scroller itself (ChatTimeline.tsx's `chat-scroll overflow-y-auto`
  // container) is now mounted — confirm it independently of the right panel.
  await expect(page.locator(".chat-scroll.overflow-y-auto")).toBeVisible();

  // The right panel (컨텍스트 패널) switches to the QuestionForm mode once the
  // "questions" event lands — the demo scenario's 2 fixed questions, both
  // rendered as radio groups (multi_select is out of scope for this fixed
  // demo payload — see QuestionCard.test.tsx for checkbox coverage).
  await expect(rightPanel.getByText("주요 사용자는 누구인가요?")).toBeVisible({ timeout: 30_000 });
  await expect(rightPanel.getByText("가장 큰 페인포인트는?")).toBeVisible();
  await expect(rightPanel.locator('input[type="checkbox"]')).toHaveCount(0);

  // Select the AI-recommended option for each of the 2 demo questions
  // (radios are visually hidden — `sr-only peer` — so the visible label text
  // is the interaction target, matching QuestionCard.tsx's <label> wrapping).
  await rightPanel.getByText("사내 PM").click();
  await rightPanel.getByText("도구 접근성").click();

  await rightPanel.getByRole("button", { name: /답변 제출/ }).click();

  // send_answers's scripted round reflects the answers in a new AI message
  // and completes the Envision stage.
  const answerMessage = timeline.getByText(/답변\(.*\)을 반영했습니다/);
  await expect(answerMessage).toBeVisible({ timeout: 30_000 });
  const sidebar = page.getByLabel("스테이지 진행 상황");
  await expect(sidebar.getByText("Envision")).toBeVisible();

  // Markdown rendering (spec §3/§4): every AI bubble renders through the
  // shared <Markdown> component (AiMessage.tsx), which always wraps its
  // output in a `.prose` container regardless of whether the demo text
  // itself contains markdown syntax — assert that wrapper is present on the
  // reflected-answer message rather than asserting on `<strong>`/heading
  // markup the LocalSandbox demo script doesn't actually emit.
  const answerBubble = answerMessage.locator("xpath=ancestor::div[contains(@class,'prose')]").first();
  await expect(answerBubble).toBeVisible();

  // The right panel drops out of "questions" mode (pendingQuestions cleared)
  // once answers are submitted — the question text disappears.
  await expect(rightPanel.getByText("주요 사용자는 누구인가요?")).toHaveCount(0);

  // No unhandled turn error surfaced in the AI bubble.
  await expect(page.getByText(/연결이 끊어졌습니다/)).toHaveCount(0);
});

// History restore across a tab switch/reload (spec §5's Task 1/5 flow) is
// NOT exercised here: local mode has no S3 session behind it, so GET
// /history always resolves to [] and a reload would simply re-show the
// welcome card — it cannot demonstrate restoring a PAST conversation. That
// round-trip only exists once a real session object is durable, i.e. against
// a microvm-mode backend with the S3 Strands session store wired up. This is
// tracked as a manual real-VM drill item in
// docs/superpowers/plans/2026-07-19-strands-drill-checklist.md (step 4's
// context-restore rehearsal), not as an automated e2e case here.

test("retired /questions and /canvas tabs redirect to /workspace", async ({ page }) => {
  const pid = `e2e-redirect-${Date.now()}`;
  const create = await page.request.post("/api/projects", { data: { project_id: pid } });
  expect(create.ok()).toBe(true);

  await page.goto(`/projects/${pid}/questions`);
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/workspace$`));

  await page.goto(`/projects/${pid}/canvas`);
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/workspace$`));
});
