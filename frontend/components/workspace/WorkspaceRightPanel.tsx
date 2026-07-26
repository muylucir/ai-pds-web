// frontend/components/workspace/WorkspaceRightPanel.tsx
import { QuestionForm } from "@/components/questions/QuestionForm";
import { PreviewPanelBody } from "@/components/canvas/PreviewPanel";
import type { QuestionsPayload, StagePayload } from "@/lib/api/types";

// Stage-name substrings that indicate the "prototype build" step is active —
// matched with `.includes` (not equality) so either the English backend
// stage name or a localized variant still routes to the preview mode (spec
// §5's mode-priority table).
const PROTOTYPE_STAGES = ["Prototype & Validation", "프로토타입"];

export type Mode = "questions" | "preview" | "artifacts";

// Mode priority (spec §5): a pending question interrupt always wins (the
// user is blocked on it); otherwise, if the prototype stage is the one
// currently in_progress, show the live preview; otherwise fall back to the
// running list of touched artifacts.
//
// `streaming` guards the questions→preview handoff. submitAnswers clears
// pendingQuestions the INSTANT the user submits, so during the prototype
// stage the panel would drop straight to "preview" mid-turn and then snap
// back to "questions" when the next question arrived — the panel visibly
// flipping to the prototype viewer on its own between answers (ui-bug:
// question2.png). A turn in flight means the agent is mid-thought and the
// next interrupt may be another question, so hold the current fallback
// instead of committing to the preview. Optional so existing call sites
// (and the settled case) keep the plain priority order.
//
// `stages` (useWorkspaceStream's accumulated "stage" events) is an
// APPEND-ONLY log — a stage's later "completed" event is a separate array
// entry, not an overwrite of its earlier "in_progress" one. Filtering the
// raw array for status==="in_progress" would therefore stay stuck on any
// stage that was EVER in_progress, even long after it completed. Reduce to
// each stage's LATEST event first (same latest-wins-by-name idea as
// StageSidebar's mergeStages) so only the CURRENT snapshot is considered.
export function deriveMode(
  pending: QuestionsPayload | null,
  stages: StagePayload[],
  streaming = false,
): Mode {
  if (pending) return "questions";
  if (streaming) return "artifacts";
  const latestByName = new Map<string, StagePayload>();
  for (const ev of stages) latestByName.set(ev.stage, ev);
  const active = [...latestByName.values()]
    .filter((s) => s.status === "in_progress")
    .map((s) => s.stage);
  if (active.some((s) => PROTOTYPE_STAGES.some((p) => s.includes(p)))) return "preview";
  return "artifacts";
}

export function WorkspaceRightPanel({
  projectId,
  pendingQuestions,
  stages,
  changedPaths,
  onSubmitAnswers,
  busy,
}: {
  projectId: string;
  pendingQuestions: QuestionsPayload | null;
  stages: StagePayload[];
  changedPaths: string[];
  onSubmitAnswers: (answers: Record<string, string>) => void;
  busy: boolean;
}) {
  const mode = deriveMode(pendingQuestions, stages, busy);
  return (
    <aside
      aria-label="컨텍스트 패널"
      className="hidden lg:flex flex-col min-w-0 min-h-0 bg-white border-l border-slate-200"
    >
      {mode === "questions" && pendingQuestions && (
        <div className="flex-1 min-h-0 overflow-y-auto p-6">
          <QuestionForm file={pendingQuestions.questions} onSubmit={onSubmitAnswers} submitting={busy} />
        </div>
      )}
      {mode === "preview" && (
        <div aria-label="프로토타입 프리뷰" className="flex-1 flex flex-col min-h-0">
          <PreviewPanelBody projectId={projectId} />
        </div>
      )}
      {mode === "artifacts" && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4">
          <p className="text-xs font-bold text-slate-400 uppercase tracking-wide mb-3">최근 산출물</p>
          {changedPaths.length === 0 ? (
            <p className="text-sm text-slate-400">아직 변경된 파일이 없습니다.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {changedPaths.map((path) => (
                <li key={path} className="rounded-lg border border-slate-200 px-3 py-2 text-slate-600 break-all">
                  {path}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </aside>
  );
}
