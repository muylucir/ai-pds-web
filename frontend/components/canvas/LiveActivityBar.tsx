// frontend/components/canvas/LiveActivityBar.tsx — a progress line pinned directly
// above the input box. While a turn runs it shows "what is happening right now and for
// how long" **on a single line only**.
//
// **Why it sits outside the conversation flow (2026-09-04 user feedback).** Previously
// this indicator was attached below the AI speech bubble, and next to it the progress-log
// accordion stayed expanded and kept growing. Once token streaming arrived and the bubble
// text started flowing at the same time, three things were moving on screen and it became
// distracting ("it's chaotic," in the user's words). So progress was taken out of the
// conversation flow and moved to a fixed line above the input box — the list belongs to
// the progress log, collapsed after the turn ends.
//
// As a result this component **belongs to the screen, not to a message**. The workspace
// and prototype build panels each place it above their own input box (AiMessage no longer
// knows about it). That is why a mode flag does not have to be passed down via `ChatTimeline`.
//
// Previously it was one line of 12px text plus a single pulsing dot inside AiMessage. What
// made that frustrating was not the amount of information but **the lack of proof of life**:
//   - When an answer paragraph arrives first, the typing dots inside the bubble disappear,
//     so even while tools keep running, the only thing moving on screen shrinks to one dot.
//   - The pulse (animate-pulse) only oscillates opacity, so it is indistinguishable from a
//     screen that has been frozen for 30 seconds.
//   - With no elapsed time, a tool that takes 3 seconds looks the same as one taking 40.
//     The user has no basis to decide "should I keep waiting, or should I refresh?"
//
// So this component shows three things together: a rotating spinner (an animation that
// changes position, so it is clearly distinguishable from a frozen screen), a localized
// label for what is being done, and elapsed time counting up by the second. The elapsed
// time is the real liveness signal — while the number is rising, it is self-evidently not stuck.
"use client";
import { useEffect, useRef, useState } from "react";

import type { Dict } from "@/lib/i18n";
import type { AgentRow } from "@/lib/protoAgents";
import type { LiveActivity } from "@/lib/chatItems";
import { useT } from "@/lib/i18n/provider";

type T = (key: keyof Dict) => string;

// Tool name → user-friendly activity text. Moved here from AiMessage (all pieces of the
// progress indicator live in one file).
//
// The two drivers send different tool names: the Claude Agent SDK sends built-in tool names
// (Write/Read/Edit/AskUserQuestion), Strands sends custom tool names (file_write/…).
// If a name is missing from the mapping, activityLabel's fallback exposes the English tool
// name as-is, so both sets are kept (needed during the AIPDS_DISCOVERY_DRIVER fallback period).
const ACTIVITY_LABEL_KEYS: Record<string, keyof Dict> = {
  // Claude Agent SDK restricted tools (enabled only via MCP or allowed_tools)
  AskUserQuestion: "activity.questions",
  Write: "activity.writing",
  Edit: "activity.writing",
  MultiEdit: "activity.writing",
  // Claude Agent SDK default tools (tools=None, so all CLI default tools are available).
  // The driver does not set tools=, so there is no restriction; the tools actually reachable
  // in a Discovery turn are listed here. envision.md's "URL로 분석(Mode B/C)" (analyze by
  // URL) requires WebFetch, and the workspace-detection stage naturally explores files with
  // Glob/Grep. If the list changes, this must be updated as well.
  Read: "activity.reading",
  Glob: "activity.searching",
  Grep: "activity.searching",
  Bash: "activity.working",
  WebFetch: "activity.fetching",
  // The tool that spawns a subagent (measured: its name is `Agent`, not the old `Task`). When
  // the orchestrating agent calls it, all it does itself is wait for the subagent —
  // leaving it to the fallback would show "Agent 실행 중" ("running Agent"), which only
  // obscures what is going on.
  Agent: "activity.delegating",
  // Custom tool exclusive to the prototype build (proto/tools.py)
  build_complete: "activity.buildFinishing",
  // Discovery has no custom tools. `report_stage` (2026-08-18) and
  // `submit_document` (2026-08-21) used to be here and both moved to PostToolUse hooks —
  // they are not tool calls, so they never appear in this list (agent/reconcile.py).
  // Strands driver (kept during the env fallback period)
  ask_questions: "activity.questions",
  file_write: "activity.writing",
  file_append: "activity.writing",
  file_read: "activity.reading",
};

/** Tool name → activity text. A pure function that takes `t` as an argument — making it
 *  a hook would bind every caller to the rules of hooks (same judgment as answerSummary).
 *
 *  There must be an indicator even before any tool has run (right after the turn starts,
 *  while the model is only thinking) — that stretch is the longest and most anxious one. */
export function activityLabel(tool: string | null | undefined, t: T): string {
  if (!tool) return t("activity.thinking");
  const key = ACTIVITY_LABEL_KEYS[tool];
  // A tool missing from the mapping exposes its name as-is — seeing even an English tool
  // name is better than not knowing what is running.
  return key ? t(key) : `${tool} ${t("activity.genericSuffix")}`;
}

/** Human-readable elapsed time. Past 60 seconds, minutes are shown too — three-digit
 *  seconds are not readable at a glance ("1m 35s" reads faster than "95s").
 *
 *  Units come from the dictionary, but the number/unit order is the same in both
 *  languages ("95초"/"95s") — if a language with different word order is added, switch to
 *  a sentence template at that point. */
export function formatElapsed(totalSeconds: number, t: T): string {
  const sec = t("activity.unitSeconds");
  const min = t("activity.unitMinutes");
  if (totalSeconds < 60) return `${totalSeconds}${sec}`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${minutes}${min}` : `${minutes}${min} ${seconds}${sec}`;
}

/** Elapsed seconds counting up every second from `startedAt` (ms).
 *
 *  **Why the start time is a parameter.** Previously it counted from mount time — when
 *  there was a single bar, that was the start of the turn, so it was correct. With
 *  subagent rows it no longer is: each row appears at a different moment, and each row
 *  must show **its own** elapsed time for that number to be proof of life (module header).
 *  Counting from mount would make a late-spawned agent look as if it had already worked a minute.
 *
 *  Why it is computed from `Date.now()` differences: counting setInterval calls undercounts
 *  real elapsed time, because the browser throttles timers when the tab goes to the
 *  background. When the user returns from another tab, it must not insist on "12s". */
function useElapsedSeconds(startedAt: number): number {
  const [seconds, setSeconds] = useState(() =>
    Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));

  useEffect(() => {
    setSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    const id = setInterval(() => {
      setSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  return seconds;
}

/** A rotating ring. Rotation is used instead of animate-pulse (which only changes opacity)
 *  because the goal is to be distinguishable from a frozen screen — only an animation that
 *  changes position clearly conveys "it is moving right now". */
function Spinner() {
  return (
    <svg
      className="w-3.5 h-3.5 shrink-0 animate-spin text-violet-600"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
      <path
        d="M8 1.5a6.5 6.5 0 0 1 6.5 6.5"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Kind of file being written → label. Same boundary as the document panel (isDocPath in
 *  useWorkspaceStream): question files become forms, audit/state files are records,
 *  and everything else is a document. */
function draftingLabel(path: string | null, t: T): string {
  if (path === null) return t("activity.writing");
  const name = path.slice(path.lastIndexOf("/") + 1);
  if (name.endsWith("-questions.md")) return t("activity.draftingQuestions");
  if (name === "audit.md" || name === "aiplc-state.md") return t("activity.recording");
  return t("activity.draftingDoc");
}

/** Activity kind → human-readable label and the **target** of that activity (file/command). A pure function.
 *
 *  Why the target is returned separately from the label: only the target can overflow the
 *  line, so truncation must apply only there. If the label were truncated with it, what is
 *  being done would disappear first. */
export function activityText(
  activity: LiveActivity, t: T,
): { label: string; target: string | null } {
  switch (activity.kind) {
    case "thinking":
      return { label: t("activity.thinking"), target: null };
    case "writing":
      return { label: t("activity.writing"), target: null };
    case "file":
      return { label: t("canvas.fileChanged"), target: activity.path };
    case "tool":
      return { label: activityLabel(activity.tool, t), target: activity.detail };
    case "drafting": {
      // The character count is the evidence of progress — elapsed time alone cannot tell a
      // document being written for over a minute from a stalled one (measured; see the
      // backend agent/tool_input_draft.py header).
      const name = activity.path ? activity.path.slice(activity.path.lastIndexOf("/") + 1) : null;
      const count = activity.chars > 0
        ? t("activity.charsCount").replace("{n}", activity.chars.toLocaleString())
        : null;
      const target = [name, count].filter(Boolean).join(" · ") || null;
      return { label: draftingLabel(activity.path, t), target };
    }
  }
}

/** One row. The first line is spinner · what is happening · elapsed time; the second line is the target (command/file).
 *
 *  Why the target moved to the second line: while it shared one line, the target was
 *  truncated after the label and was barely readable. The target is the most concrete information in this bar.
 *
 *  It is intentional that the head line and agent rows are **the same component**: if a
 *  row behaves differently from the head line (no spinner, or elapsed time not rising), it
 *  does not read as progress, and that is exactly the symptom this feature sets out to fix. */
function ActivityLine({
  label, target, startedAt, nested = false, twoLine = true, testId,
}: {
  label: string;
  target: string | null;
  startedAt: number;
  nested?: boolean;
  // Put the target (command/file) on the second line. Even with no target, the line's height
  // is kept empty — if the bar toggled between 1 and 2 lines on every stage change, the input
  // box below would jump up and down. Only the head row while subagents run turns it off
  // (that row only states the count; the rows right below it say the concrete work).
  twoLine?: boolean;
  testId?: string;
}) {
  const t = useT();
  const elapsed = useElapsedSeconds(startedAt);
  return (
    <div className={nested ? "pl-4" : ""}>
      <div className="flex items-center gap-2">
        {/* `⎿` says with a single character that this row is work belonging to the line above —
            the same notation as Claude Code's tree; indentation alone cannot distinguish it from a list.
            The cell width is fixed so the second line starts at the same position as the label's first character. */}
        {nested && (
          <span className="shrink-0 w-3 text-xs text-violet-300" aria-hidden="true">⎿</span>
        )}
        <Spinner />
        <span
          data-testid={testId}
          className={`flex-1 min-w-0 truncate text-xs ${
            nested ? "text-violet-600" : "font-medium text-violet-700"}`}
        >
          {label}
          {!twoLine && target && (
            <span className="ml-1.5 font-normal font-mono text-violet-500">· {target}</span>
          )}
        </span>
        <span className="shrink-0 text-xs text-violet-400 tabular-nums" aria-hidden="true">
          {formatElapsed(elapsed, t)}
        </span>
      </div>
      {twoLine && (
        // On overflow only this line is truncated — what is being done (first line) remains. Full text via title.
        <p
          data-testid={testId ? `${testId}-target` : undefined}
          title={target ?? undefined}
          className={`${nested ? "pl-[2.625rem]" : "pl-[1.375rem]"} min-h-4 truncate text-xs leading-4 font-mono text-violet-500`}
        >
          {target ?? "\u00a0"}
        </p>
      )}
    </div>
  );
}

export function LiveActivityBar({
  activity, agents = [],
}: { activity: LiveActivity; agents?: readonly AgentRow[] }) {
  const t = useT();
  // Being mounted is the same as being in progress — the caller (screen) controls mounting
  // via streaming, so it is not judged again here. Read only once on the first render.
  const mountedAt = useRef(Date.now());

  // While subagents run, the head line states the **count**. During that stretch all the
  // orchestrating agent does is wait for the subagents, and what the user wants to know
  // is "how many are working" — previously that single line flickered between the
  // subagents' tool names, and that is why the screen looked stuck.
  const head = agents.length > 0
    ? { label: `${agents.length}${t("activity.agentsWorkingSuffix")}`, target: null }
    : activityText(activity, t);

  return (
    // role="status" announces activity changes to screen readers. Elapsed time is
    // excluded via aria-hidden — reading it every second would drown out label changes.
    //
    // It is a strip attached above the input box, so it is full-width with only a top border —
    // it must read as a strip between the conversation area and the compose area; if it looks
    // like a bubble it becomes part of the conversation again.
    <div
      role="status"
      // max-h: even with an unusually large number of subagents it does not consume the whole
      // conversation area — normally (around 3) all are visible, and it scrolls inside the bar only on overflow.
      className="shrink-0 max-h-[40vh] overflow-y-auto border-t border-violet-200 bg-violet-50 px-4 py-2 space-y-1.5"
    >
      <ActivityLine
        label={head.label}
        target={head.target}
        startedAt={mountedAt.current}
        twoLine={agents.length === 0}
        testId="live-what"
      />
      {agents.map((agent) => (
        <ActivityLine
          key={agent.id}
          nested
          // The name is the work assigned to that agent (the backend passes the Agent tool's
          // description through as-is). Rows that received no label use a fallback name —
          // a row without a name is still better if you can see it running.
          label={agent.label ?? t("activity.agentUnnamed")}
          // If there is a target, it is the most concrete (which file it is touching). If not,
          // at least say which tool it is running.
          target={agent.detail ?? (agent.tool ? activityLabel(agent.tool, t) : null)}
          startedAt={agent.startedAt}
        />
      ))}
    </div>
  );
}
