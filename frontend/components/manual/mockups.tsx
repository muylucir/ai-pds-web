"use client";
// frontend/components/manual/mockups.tsx — the manual's screen illustrations.
//
// **No new wording is written here.** Everything is read from the app dictionary via t() —
// the reason is in the header of chrome.tsx. Writing Korean sentences directly here makes
// lib/i18n/noHardcodedKorean.test.ts fail immediately.
//
// If real screenshots are added, each mockup can be replaced with an <img> inside the <figure>.
// The block kind (`{kind:"mockup"}`) and the caption can be used as is.
import { formatElapsed } from "@/components/canvas/LiveActivityBar";
import { LANGUAGE_LABEL } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import type { MockupId } from "@/content/manual";

import { Badge, Btn, Field, Frame, Lines, Panel, PanelTitle } from "./chrome";

function ProjectCreate() {
  const t = useT();
  return (
    <Frame>
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <Field label={t("project.id")} value={t("project.idPlaceholder")} />
          <Field label={t("project.nameOptional")} value={t("project.namePlaceholder")} />
          <Field label={t("project.defaultModel")} value="Opus 4.8" />
          {/* Language names are written in the language itself — LANGUAGE_LABEL is the single
              source for that notation (same values as the header badge and project list). */}
          <Field
            label={t("project.language")}
            value={`${LANGUAGE_LABEL.ko} / ${LANGUAGE_LABEL.en}`}
          />
        </div>
        <p className="text-[9px] text-slate-400">{t("project.idCharsHint")}</p>
        <div className="text-right">
          <Btn tone="primary">{t("project.create")}</Btn>
        </div>
      </div>
    </Frame>
  );
}

// The names of the 6 phases are backend constants (aipds/capabilities.py), and the ko screen does not
// translate them either — so here too those names are used as is rather than via the dictionary. The state
// is Path A (starting from PR/FAQ) having reached Prototype: Intake and Prioritize are skipped.
const PHASES: { name: string; status: "done" | "active" | "skipped" | "pending" }[] = [
  { name: "Envision", status: "done" },
  { name: "Use Case Intake", status: "skipped" },
  { name: "Prioritize", status: "skipped" },
  { name: "Prototype", status: "active" },
  { name: "Product Strategy", status: "pending" },
  { name: "Go-to-Market", status: "pending" },
];

function PhaseList({ withBadges = false }: { withBadges?: boolean }) {
  const t = useT();
  return (
    <ul className="space-y-1">
      {PHASES.map((p, i) => (
        <li key={p.name} className="flex items-center gap-1.5">
          <span
            className={
              p.status === "done" ? "text-emerald-600"
                : p.status === "active" ? "text-violet-600"
                  : "text-slate-300"
            }
          >
            {p.status === "done" ? "✓" : p.status === "active" ? "●" : p.status === "skipped" ? "–" : i + 1}
          </span>
          <span
            className={`min-w-0 flex-1 truncate ${
              p.status === "active" ? "font-semibold text-violet-800"
                : p.status === "skipped" ? "text-slate-300" : "text-slate-500"
            }`}
          >
            {p.name}
          </span>
          {p.status === "skipped" && (
            <span className="shrink-0 text-[8px] text-slate-300">{t("canvas.capabilityNotApplicable")}</span>
          )}
          {withBadges && p.status === "done" && <Badge tone="emerald">{t("dash.stageDone")}</Badge>}
          {withBadges && p.status === "active" && <Badge tone="violet">{t("dash.stageInProgress")}</Badge>}
        </li>
      ))}
    </ul>
  );
}

function Workspace() {
  const t = useT();
  return (
    <Frame>
      {/* The real screen's 1:5:5 grid — phase / conversation / questions & context. Generated documents are
          not a column but a drawer opened by the handle tab at the far right (the grid leaves room for the tab). */}
      <div className="relative pr-6">
        <div className="grid grid-cols-[1fr_1.6fr_1.6fr] gap-2">
          <Panel>
            <PanelTitle>{t("canvas.discoveryProgress")}</PanelTitle>
            <PhaseList />
            <p className="mt-1.5 text-[9px] text-slate-400">1 / 4 {t("canvas.stageUnit")}</p>
          </Panel>

          <Panel>
            <PanelTitle>{t("canvas.timelineLabel")}</PanelTitle>
            <div className="space-y-1.5">
              <div className="ml-6 rounded-md bg-violet-50 p-1.5">
                <Lines n={1} />
              </div>
              <div className="mr-6 rounded-md bg-slate-50 p-1.5">
                <Lines n={3} />
              </div>
              <Badge tone="violet">{t("chat.questionsPresented")}</Badge>
            </div>
            {/* Progress line above the input box — first line what is happening and elapsed time, second line the target. */}
            <div className="mt-2 text-[9px] text-violet-700">
              <div className="flex items-center justify-between">
                <span className="font-medium">{t("activity.draftingDoc")}</span>
                <span className="text-violet-400">{formatElapsed(42, t)}</span>
              </div>
              <p className="font-mono text-violet-500">
                PR-FAQ.md · {t("activity.charsCount").replace("{n}", "1,234")}
              </p>
            </div>
            <div className="mt-1 flex items-center gap-1 rounded-md border border-slate-300 px-1.5 py-1">
              <span className="text-slate-400">🖇</span>
              <span className="flex-1 truncate text-[9px] text-slate-400">
                {t("canvas.messagePlaceholder")}
              </span>
              <Btn tone="quiet">■</Btn>
              <Btn tone="primary">{t("canvas.send")}</Btn>
            </div>
            <p className="mt-1 text-[9px] text-slate-400">{t("chat.auditNotice")}</p>
          </Panel>

          {/* Right column — the answer sheet when a question arrives, otherwise the preview or the latest artifact. */}
          <Panel>
            <PanelTitle>{t("ws.contextPanelAria")}</PanelTitle>
            <p className="mb-1.5 inline-block rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[9px] text-violet-700">
              {t("ws.openDocNamed").replace("{name}", "PR-FAQ.md")}
            </p>
            <div className="mb-1.5 flex flex-wrap items-center gap-1">
              <Badge tone="violet">{t("q.legend")} 3</Badge>
              <Badge tone="slate">{t("q.singleSelectBadge")}</Badge>
            </div>
            <ul className="space-y-1">
              <li className="flex items-center gap-1.5 rounded border border-violet-300 bg-violet-50 px-1.5 py-1">
                <span className="text-violet-600">◉</span>
                <span className="h-1.5 flex-1 rounded bg-violet-200" />
              </li>
              <li className="flex items-center gap-1.5 rounded border border-slate-200 px-1.5 py-1">
                <span className="text-slate-300">○</span>
                <span className="h-1.5 flex-1 rounded bg-slate-100" />
              </li>
            </ul>
            <div className="mt-1.5 text-right">
              <Btn tone="primary">{t("q.submitAnswers")}</Btn>
            </div>
            <p className="mt-1.5 text-[9px] text-slate-400">
              {t("canvas.previewTitle")} · {t("ws.recentArtifacts")}
            </p>
          </Panel>
        </div>

        {/* Drawer handle — if a new document is written while it is closed, it fills with "new document". */}
        <div className="absolute right-0 top-3 flex w-5 flex-col items-center gap-1 rounded-l-md bg-violet-600 py-2 text-[9px] font-bold text-white">
          <span className="[writing-mode:vertical-rl]">{t("ws.docTabNew")}</span>
          <span className="rounded-full bg-white px-1 text-[8px] text-violet-700">3</span>
        </div>
      </div>
    </Frame>
  );
}

function QuestionSheet() {
  const t = useT();
  return (
    <Frame>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-slate-600">{t("q.legend")} 3</span>
          <span className="text-[10px] text-slate-400">2 {t("q.answeredCount")}</span>
        </div>
        <Panel>
          <div className="mb-1.5 flex items-center gap-1.5">
            <Badge tone="slate">{t("q.category")}</Badge>
            <Badge tone="violet">{t("q.singleSelectBadge")}</Badge>
          </div>
          <Lines n={1} />
          <ul className="mt-2 space-y-1">
            <li className="flex items-center gap-1.5 rounded border border-violet-300 bg-violet-50 px-1.5 py-1">
              <span className="text-violet-600">◉</span>
              <span className="h-1.5 flex-1 rounded bg-violet-200" />
              <Badge tone="amber">{t("q.aiRecommended")}</Badge>
            </li>
            <li className="flex items-center gap-1.5 rounded border border-slate-200 px-1.5 py-1">
              <span className="text-slate-300">○</span>
              <span className="h-1.5 flex-1 rounded bg-slate-200" />
            </li>
            <li className="flex items-center gap-1.5 rounded border border-slate-200 px-1.5 py-1">
              <span className="text-slate-300">○</span>
              <span className="text-[10px] text-slate-500">{t("q.otherOption")}</span>
            </li>
          </ul>
          <p className="mt-1.5 rounded border border-slate-200 px-1.5 py-1 text-[9px] text-slate-400">
            {t("q.notePlaceholder")}
          </p>
        </Panel>
        <div className="flex items-center justify-between">
          <span className="text-[9px] text-slate-400">{t("q.auditNotice")}</span>
          <Btn tone="primary">{t("q.submitAnswers")}</Btn>
        </div>
      </div>
    </Frame>
  );
}

function ApprovalGate() {
  const t = useT();
  return (
    <Frame>
      <div className="space-y-2">
        {/* The gate appears at the top of the screen when discovery-document.md is open and not yet approved. */}
        <Panel className="border-amber-200 bg-amber-50">
          <div className="flex items-center gap-1.5">
            <p className="font-semibold text-amber-900">{t("review.gateTitle")}</p>
            <Badge tone="amber">{t("review.badgeDraft")}</Badge>
          </div>
          <p className="mb-2 mt-0.5 text-[9px] text-amber-900">{t("review.gateIntro")}</p>
          <div className="flex justify-end gap-1.5">
            <Btn tone="ghost">{t("review.gateReviseBtn")}</Btn>
            <Btn tone="primary">{t("review.gateApproveBtn")}</Btn>
          </div>
        </Panel>
        <div className="flex justify-end gap-1.5">
          <Btn tone="quiet">{t("page.downloadAllZip")}</Btn>
          <Btn tone="quiet">{t("page.downloadMd")}</Btn>
        </div>
        {/* File list / body / validation summary — the real screen's three columns. */}
        <div className="grid grid-cols-[0.8fr_2fr_1fr] gap-2">
          <Panel>
            <p className="text-[9px] text-slate-500">aiplc-docs/</p>
            <p className="mt-1 truncate rounded bg-violet-50 px-1 text-[9px] text-violet-700">discovery-document.md</p>
            <Lines n={2} className="mt-1" />
          </Panel>
          <Panel>
            <Lines n={5} />
          </Panel>
          <Panel className="border-violet-200 bg-violet-50/40">
            <PanelTitle>{t("review.verificationSummary")}</PanelTitle>
            <Lines n={2} />
            <PanelTitle>{t("review.gateHistory")}</PanelTitle>
            <Lines n={1} />
          </Panel>
        </div>
      </div>
    </Frame>
  );
}

function PrototypeCard() {
  const t = useT();
  return (
    <Frame>
      <div className="space-y-2">
        <Panel>
          {/* The title slot is the spec's product name — it differs per project, so it is drawn as a bar,
              with the spec path on the line below (same layout as the real card). */}
          <div className="flex items-center gap-2">
            <span className="h-2 w-24 rounded bg-slate-300" />
            <Badge tone="emerald">{t("proto.statusRunning")}</Badge>
            <span className="text-[11px] text-slate-500">
              {t("proto.surveyResponses").replace("{n}", "3")}
            </span>
          </div>
          <p className="mt-0.5 text-[11px] text-slate-400">PROTOTYPE-*.md</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Btn tone="primary">{t("proto.openPreview")}</Btn>
            <Btn tone="ghost">{t("proto.copyLink")}</Btn>
            <Btn tone="ghost">{t("proto.modify")}</Btn>
            <Btn tone="quiet">{t("proto.stopHosting")}</Btn>
            <Btn tone="ghost">{t("proto.logs")}</Btn>
            <Btn tone="ghost">{t("proto.download")}</Btn>
            <Btn tone="danger">{t("proto.reset")}</Btn>
            <Btn tone="ghost">{t("proto.survey")}</Btn>
          </div>
        </Panel>
        <Panel className="border-emerald-300 bg-emerald-50/40">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-emerald-700">{t("proto.buildComplete")}</span>
            <Badge tone="emerald">{t("proto.done")}</Badge>
          </div>
          <div className="mt-1">
            <PanelTitle>{t("proto.remainingWork")}</PanelTitle>
            <Lines n={2} />
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Btn tone="primary">{t("proto.startHosting")}</Btn>
            <Btn tone="ghost">{t("proto.continueImproving")}</Btn>
            <Btn tone="quiet">{t("proto.close")}</Btn>
          </div>
        </Panel>
      </div>
    </Frame>
  );
}

function SurveyPanel() {
  const t = useT();
  return (
    <Frame>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-slate-600">{t("survey.title")}</span>
          <div className="flex gap-1.5">
            <Btn tone="ghost">{t("survey.refresh")}</Btn>
            <Btn tone="ghost">{t("survey.copyLink")}</Btn>
            <Btn tone="quiet">{t("survey.close")}</Btn>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Panel>
            <PanelTitle>{t("survey.responseCount").replace("{n}", "24")}</PanelTitle>
            <div className="space-y-1">
              <div className="flex items-center gap-1.5">
                <span className="w-8 text-[9px] text-slate-400">{t("survey.mean")}</span>
                <span className="h-2 flex-1 rounded bg-slate-100">
                  <span className="block h-2 w-3/4 rounded bg-violet-400" />
                </span>
                <span className="text-[9px] text-slate-500">4.1</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-8 text-[9px] text-slate-400">{t("survey.mean")}</span>
                <span className="h-2 flex-1 rounded bg-slate-100">
                  <span className="block h-2 w-1/2 rounded bg-violet-300" />
                </span>
                <span className="text-[9px] text-slate-500">3.2</span>
              </div>
            </div>
            <p className="mt-1.5 text-[9px] text-slate-400">
              {t("survey.freeTextResponses").replace("{n}", "7")}
            </p>
          </Panel>
          <Panel>
            <PanelTitle>{t("survey.generateHint")}</PanelTitle>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <Btn tone="primary">{t("survey.synthesize")}</Btn>
              <Btn tone="ghost">{t("survey.exportCsv")}</Btn>
              <Btn tone="ghost">{t("survey.createNew")}</Btn>
            </div>
            <p className="mt-1.5 text-[9px] text-slate-400">{t("survey.exportForAll")}</p>
          </Panel>
        </div>
      </div>
    </Frame>
  );
}

function Dashboard() {
  const t = useT();
  return (
    <Frame>
      <div className="space-y-2">
        <div className="grid grid-cols-4 gap-1.5">
          {[
            t("dash.overallProgress"),
            t("dash.completedStages"),
            t("dash.questionRecords"),
            t("dash.generatedArtifacts"),
          ].map((label) => (
            <Panel key={label}>
              <p className="text-[9px] text-slate-400">{label}</p>
              <p className="mt-0.5 text-sm font-bold text-violet-700">—</p>
            </Panel>
          ))}
        </div>
        <div className="grid grid-cols-[1.4fr_1fr] gap-2">
          <Panel>
            <PanelTitle>{t("dash.stageProgressTitle")}</PanelTitle>
            <PhaseList withBadges />
            <p className="mt-1.5 text-[9px] text-slate-400">{t("dash.adaptiveNote")}</p>
          </Panel>
          <Panel>
            <PanelTitle>{t("dash.recentActivity")}</PanelTitle>
            <Lines n={4} />
            <p className="mt-2 text-[9px] text-violet-700">{t("dash.continueAnswering")}</p>
          </Panel>
        </div>
      </div>
    </Frame>
  );
}

// id → component. Record requires **every** MockupId, so adding an id and
// forgetting its illustration makes compilation fail.
export const MOCKUPS: Record<MockupId, () => React.ReactElement> = {
  "project-create": ProjectCreate,
  workspace: Workspace,
  "question-sheet": QuestionSheet,
  "approval-gate": ApprovalGate,
  "prototype-card": PrototypeCard,
  "survey-panel": SurveyPanel,
  dashboard: Dashboard,
};
