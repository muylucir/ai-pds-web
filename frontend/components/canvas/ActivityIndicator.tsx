// frontend/components/canvas/ActivityIndicator.tsx — 턴이 도는 동안 "지금
// 살아있다"를 보여주는 진행 표시.
//
// 종전에는 AiMessage 안에 12px 텍스트 한 줄 + 맥동하는 점 하나였다. 그것이
// 답답했던 이유는 정보량이 아니라 **살아있음의 증거가 없다**는 것이다:
//   - 답변 문단이 먼저 도착하면 말풍선 안 타이핑 점이 사라져, 도구가 계속
//     도는데도 화면에서 움직이는 것이 점 하나로 줄어든다.
//   - 맥동(animate-pulse)은 투명도만 오가므로 30초 멈춘 화면과 구분되지 않는다.
//   - 경과 시간이 없어서 3초 걸리는 도구와 40초 걸리는 도구가 똑같아 보인다.
//     사용자가 "기다려도 되는 건가, 새로고침해야 하나"를 판단할 근거가 없다.
//
// 그래서 이 컴포넌트는 세 가지를 함께 보여준다: 회전하는 스피너(위치가 바뀌는
// 애니메이션이라 정지 화면과 확실히 구분된다), 무슨 일을 하는지의 한글 라벨,
// 그리고 초 단위로 올라가는 경과 시간. 경과 시간이 실질적인 생존 신호다 —
// 숫자가 올라가는 동안은 멈춘 것이 아니라는 것이 자명하다.
"use client";
import { useEffect, useRef, useState } from "react";

import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

type T = (key: keyof Dict) => string;

// 도구명 → 사용자 친화 활동 문구. AiMessage에서 이동해 왔다(진행 표시의 모든
// 조각을 한 파일에 둔다).
//
// 두 드라이버가 서로 다른 도구 이름을 보낸다: Claude Agent SDK는 내장 도구명
// (Write/Read/Edit/AskUserQuestion), Strands는 자작 도구명(file_write/…).
// 매핑에 없으면 activityLabel의 폴백이 영어 도구명을 그대로 노출하므로 양쪽을
// 모두 둔다(PATHFINDER_DISCOVERY_DRIVER 폴백 기간 동안 필요).
const ACTIVITY_LABEL_KEYS: Record<string, keyof Dict> = {
  // Claude Agent SDK 제한된 도구 (MCP 또는 allowed_tools로만 활성화)
  AskUserQuestion: "activity.questions",
  Write: "activity.writing",
  Edit: "activity.writing",
  MultiEdit: "activity.writing",
  // Claude Agent SDK 기본 도구 (tools=None이므로 CLI 기본 도구 전체 사용 가능).
  // 드라이버가 tools=를 설정하지 않아 제한이 없으므로, Discovery 턴에서 실제로
  // 도달 가능한 도구들을 여기 둔다. envision.md의 "URL로 분석(Mode B/C)"은
  // WebFetch가 필수이고, workspace-detection 단계는 Glob/Grep로 파일 탐색이
  // 자연스럽다. 목록이 바뀌면 여기도 함께 갱신해야 한다.
  Read: "activity.reading",
  Glob: "activity.searching",
  Grep: "activity.searching",
  Bash: "activity.working",
  WebFetch: "activity.fetching",
  // 프로토타입 빌드 전용 커스텀 도구(proto/tools.py)
  build_complete: "activity.buildFinishing",
  // 양쪽 드라이버 공통 커스텀 도구
  report_stage: "activity.reportStage",
  submit_document: "activity.submitDocument",
  // Strands 드라이버 (env 폴백 기간 유지)
  ask_questions: "activity.questions",
  file_write: "activity.writing",
  file_append: "activity.writing",
  file_read: "activity.reading",
};

/** 도구명 → 활동 문구. `t`를 인자로 받는 순수 함수다 — 훅으로 만들면 이 함수를
 *  쓰는 곳이 모두 훅 규칙에 묶인다(answerSummary와 같은 판단).
 *
 *  도구가 아직 하나도 실행되지 않은 구간(턴 시작 직후 모델이 생각만 하는 동안)
 *  에도 표시가 있어야 한다 — 그 구간이 가장 길고 가장 불안한 구간이다. */
export function activityLabel(tool: string | null | undefined, t: T): string {
  if (!tool) return t("activity.thinking");
  const key = ACTIVITY_LABEL_KEYS[tool];
  // 매핑에 없는 도구는 이름을 그대로 노출한다 — 무엇이 도는지 모르는 것보다
  // 영어 도구명이라도 보이는 편이 낫다.
  return key ? t(key) : `${tool} ${t("activity.genericSuffix")}`;
}

/** 사람이 읽는 경과 시간. 60초를 넘기면 분을 함께 보여준다 — 세 자리 초는
 *  한눈에 크기가 읽히지 않는다("95초"보다 "1분 35초"가 빠르다).
 *
 *  단위를 딕셔너리에서 가져오되 숫자와 단위의 배치는 두 언어가 같다("95초"/
 *  "95s") — 어순이 다른 언어를 넣게 되면 그때 문장 템플릿으로 바꾼다. */
export function formatElapsed(totalSeconds: number, t: T): string {
  const sec = t("activity.unitSeconds");
  const min = t("activity.unitMinutes");
  if (totalSeconds < 60) return `${totalSeconds}${sec}`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${minutes}${min}` : `${minutes}${min} ${seconds}${sec}`;
}

/** `active`인 동안 1초마다 올라가는 경과 초. 비활성이 되면 0으로 되돌린다.
 *
 *  `Date.now()` 차이로 계산하는 이유: setInterval의 호출 횟수를 세면 탭이
 *  백그라운드로 갔을 때 브라우저가 타이머를 늦춰(throttle) 실제 경과보다 적게
 *  센다. 사용자가 다른 탭을 보다 돌아왔을 때 "12초"라고 우기면 안 된다. */
function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  const startedAt = useRef<number | null>(null);

  useEffect(() => {
    if (!active) {
      startedAt.current = null;
      setSeconds(0);
      return;
    }
    startedAt.current = Date.now();
    setSeconds(0);
    const id = setInterval(() => {
      if (startedAt.current !== null) {
        setSeconds(Math.floor((Date.now() - startedAt.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(id);
  }, [active]);

  return seconds;
}

/** 회전하는 링. animate-pulse(투명도만 변함) 대신 회전을 쓰는 이유는 정지
 *  화면과의 구분이 목적이기 때문이다 — 위치가 변하는 애니메이션만이 "지금
 *  움직이고 있다"를 명확히 전달한다. */
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

export function ActivityIndicator({ tool }: { tool: string | null | undefined }) {
  const t = useT();
  // 마운트되어 있는 동안이 곧 진행 중인 동안이다 — 호출자(AiMessage)가
  // item.streaming으로 마운트를 제어하므로, 여기서 다시 판단하지 않는다.
  const elapsed = useElapsedSeconds(true);

  return (
    // role="status"로 스크린리더에 활동 변화를 알린다. 경과 시간은
    // aria-hidden으로 제외한다 — 1초마다 읽어주면 라벨 변화를 덮어버린다.
    <div
      role="status"
      className="mt-2 inline-flex items-center gap-2 rounded-full border border-violet-200 bg-violet-50 pl-2.5 pr-3 py-1.5"
    >
      <Spinner />
      <span className="text-xs font-medium text-violet-700">{activityLabel(tool, t)}</span>
      <span className="text-xs text-violet-400 tabular-nums" aria-hidden="true">
        {formatElapsed(elapsed, t)}
      </span>
    </div>
  );
}
