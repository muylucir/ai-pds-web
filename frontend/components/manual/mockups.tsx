"use client";
// frontend/components/manual/mockups.tsx — 매뉴얼의 화면 그림.
//
// **문구를 새로 쓰지 않는다.** 전부 t()로 앱 딕셔너리에서 읽는다 —
// chrome.tsx의 머리말에 이유가 있다. 여기에 한국어 문장을 직접 쓰면
// lib/i18n/noHardcodedKorean.test.ts가 즉시 실패한다.
//
// 실촬 스크린샷을 넣게 되면 각 목업을 <figure> 안에서 <img>로 바꾸면 된다.
// 블록 종류(`{kind:"mockup"}`)와 캡션은 그대로 쓸 수 있다.
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
          {/* 언어 이름은 그 언어 자체로 적는다 — LANGUAGE_LABEL이 그 표기의
              단일 출처다(헤더 배지·프로젝트 목록과 같은 값). */}
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

function Workspace() {
  const t = useT();
  return (
    <Frame>
      <div className="grid grid-cols-[1fr_1.6fr_1fr] gap-2">
        <Panel>
          <PanelTitle>{t("canvas.discoveryProgress")}</PanelTitle>
          <ul className="space-y-1">
            <li className="flex items-center gap-1">
              <span className="text-emerald-600">✓</span>
              <span className="h-1.5 flex-1 rounded bg-slate-200" />
            </li>
            <li className="flex items-center gap-1">
              <span className="text-violet-600">●</span>
              <span className="h-1.5 flex-1 rounded bg-violet-200" />
            </li>
            <li className="flex items-center gap-1">
              <span className="text-slate-300">○</span>
              <span className="h-1.5 flex-1 rounded bg-slate-100" />
            </li>
          </ul>
          <p className="mt-2 text-[9px] text-slate-400">{t("canvas.sidebarAdaptive")}</p>
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
          <div className="mt-2 flex items-center gap-1 rounded-md border border-slate-300 px-1.5 py-1">
            <span className="text-slate-400">🖇</span>
            <span className="flex-1 truncate text-[9px] text-slate-400">
              {t("canvas.messagePlaceholder")}
            </span>
            <Btn tone="quiet">■</Btn>
            <Btn tone="primary">{t("canvas.send")}</Btn>
          </div>
          <p className="mt-1 text-[9px] text-slate-400">{t("chat.auditNotice")}</p>
        </Panel>

        <Panel>
          <PanelTitle>{t("ws.generatedDocs")}</PanelTitle>
          <ul className="space-y-1 text-[10px] text-slate-500">
            <li className="rounded bg-slate-50 px-1.5 py-1">PR-FAQ.md</li>
            <li className="rounded bg-slate-50 px-1.5 py-1">use-cases.md</li>
            <li className="rounded bg-violet-50 px-1.5 py-1 text-violet-700">
              PROTOTYPE-*.md
            </li>
          </ul>
          <p className="mt-2 text-[9px] text-slate-400">{t("ws.recentArtifacts")}</p>
        </Panel>
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
        <div className="flex items-center gap-1.5">
          <Badge tone="amber">{t("review.badgeDraft")}</Badge>
          <span className="text-[10px] text-slate-400">discovery-document.md</span>
        </div>
        <Panel>
          <Lines n={4} />
        </Panel>
        <Panel className="border-violet-200 bg-violet-50/40">
          <PanelTitle>{t("review.verificationSummary")}</PanelTitle>
          <Lines n={2} />
        </Panel>
        <Panel className="border-violet-300">
          <p className="mb-1 font-semibold text-slate-700">{t("review.gateTitle")}</p>
          <p className="mb-2 text-[9px] text-slate-500">{t("review.gateIntro")}</p>
          <div className="flex gap-1.5">
            <Btn tone="ghost">{t("review.gateReviseBtn")}</Btn>
            <Btn tone="primary">{t("review.gateApproveBtn")}</Btn>
          </div>
        </Panel>
        <div className="flex gap-1.5">
          <Btn tone="quiet">{t("page.downloadMd")}</Btn>
          <Btn tone="quiet">{t("page.downloadAllZip")}</Btn>
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
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-600">PROTOTYPE-*.md</span>
            <Badge tone="emerald">{t("proto.statusRunning")}</Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Btn tone="primary">{t("proto.openPreview")}</Btn>
            <Btn tone="ghost">{t("proto.copyLink")}</Btn>
            <Btn tone="ghost">{t("proto.survey")}</Btn>
            <Btn tone="ghost">{t("proto.download")}</Btn>
            <Btn tone="ghost">{t("proto.logs")}</Btn>
            <Btn tone="quiet">{t("proto.stopHosting")}</Btn>
            <Btn tone="danger">{t("proto.reset")}</Btn>
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
            <ul className="space-y-1.5">
              <li className="flex items-center gap-1.5">
                <span className="text-emerald-600">✓</span>
                <span className="h-1.5 flex-1 rounded bg-slate-200" />
                <Badge tone="emerald">{t("dash.stageDone")}</Badge>
              </li>
              <li className="flex items-center gap-1.5">
                <span className="text-violet-600">●</span>
                <span className="h-1.5 flex-1 rounded bg-violet-200" />
                <Badge tone="violet">{t("dash.stageInProgress")}</Badge>
              </li>
              <li className="flex items-center gap-1.5">
                <span className="text-slate-300">○</span>
                <span className="h-1.5 flex-1 rounded bg-slate-100" />
              </li>
            </ul>
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

// id → 컴포넌트. Record가 MockupId를 **전부** 요구하므로, id를 추가하고
// 그림을 잊으면 컴파일이 실패한다.
export const MOCKUPS: Record<MockupId, () => React.ReactElement> = {
  "project-create": ProjectCreate,
  workspace: Workspace,
  "question-sheet": QuestionSheet,
  "approval-gate": ApprovalGate,
  "prototype-card": PrototypeCard,
  "survey-panel": SurveyPanel,
  dashboard: Dashboard,
};
