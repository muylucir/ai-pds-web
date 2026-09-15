// frontend/lib/protoAgents.ts — 지금 도는 서브에이전트들의 행.
//
// **왜 목록인가.** 입력창 위 고정 줄은 "가장 마지막에 일어난 일" 하나를 보여준다
// (liveActivity.ts) — 그것이 병렬 작업에서 정확히 실패하는 모양이다. 서브에이전트
// 셋이 도는 동안 그 한 줄은 셋 사이에서 깜빡이고, 어느 것도 진행으로 읽히지 않는다.
// 사용자가 받은 인상이 바로 그것이었다("멈춰 있는 것 같다"). 그래서 병렬 구간에는
// 행이 여러 개여야 하고, 각 행이 **자기** 경과 시간을 가져야 한다 — 숫자가 올라가는
// 것이 살아있음의 증거라는 LiveActivityBar 헤더의 판단이 행마다 성립해야 한다.
//
// 순수 리듀서다(훅이 아니다). 그래야 병합 규칙을 화면 없이 검증할 수 있고, 그
// 규칙에 이 파일이 존재하는 이유가 다 들어 있다.
import type { AgentActivityPayload } from "@/lib/api/types";

export interface AgentRow {
  /** 행의 키(백엔드의 task_id). */
  id: string;
  /** 그 에이전트가 맡은 일. started의 label — 없으면 null. */
  label: string | null;
  /** 방금 돌린 도구 이름. 라벨은 화면이 UI 언어로 만든다. */
  tool: string | null;
  /** 그 도구의 대상(파일 경로 등). */
  detail: string | null;
  /** 이 행이 열린 시각(ms). 행마다 경과 시간이 다르므로 행이 갖는다. */
  startedAt: number;
  /** 끝났으면 그 결과. 도는 동안에는 null. */
  status: string | null;
  /** 끝나면서 남긴 한 줄. */
  summary: string | null;
}

/** `agent_activity` 한 건을 행 목록에 접는다. 새 배열을 돌려준다(입력 불변).
 *
 *  `now`를 인자로 받는 이유: `Date.now()`를 안에서 부르면 경과 시간을 검증하는
 *  테스트가 시계에 의존한다.
 *
 *  ## 병합 규칙 (이 함수의 요점)
 *
 *  한 에이전트의 도구·대상이 **두 경로로** 온다:
 *
 *    - `TaskProgressMessage` → `tool`만 (대상 없음)
 *    - 서브에이전트의 `ToolUseBlock` → `tool` + `detail`
 *
 *  실측 순서는 고정되지 않는다(둘 다 목격됨). 그래서 `detail`을 매번 덮으면 대상이
 *  깜빡이고, 매번 유지하면 다음 도구에 이전 도구의 대상이 붙는다. 규칙은:
 *
 *    - `detail`이 오면 → 그 값을 쓴다
 *    - `detail`이 없고 `tool`이 **바뀌었으면** → 대상을 지운다(이전 도구의 것이다)
 *    - `detail`이 없고 `tool`이 그대로면 → 이전 대상을 유지한다
 *
 *  그래서 백엔드가 None 필드를 키째로 빼는 것이 계약이다 — null로 오면 첫 규칙에
 *  걸려 매 하트비트가 대상을 지운다.
 */
export function applyAgentActivity(
  rows: readonly AgentRow[],
  payload: AgentActivityPayload,
  now: number,
): AgentRow[] {
  const id = payload.task_id;
  // task_id가 없는 갱신은 어느 행의 것인지 알 수 없다 — 무시한다(빈 키로 행을
  // 만들면 서로 다른 에이전트들이 한 행에 뭉친다).
  if (!id) return rows as AgentRow[];

  const index = rows.findIndex((r) => r.id === id);
  if (index === -1) {
    // `started`를 못 본 갱신도 행을 연다. 프레임이 순서를 잃거나 사용자가 턴 중간에
    // 붙었을 때 진행 중인 에이전트를 숨기는 것보다, 라벨 없는 행이 낫다.
    return [
      ...rows,
      {
        id,
        label: payload.label ?? null,
        tool: payload.tool ?? null,
        detail: payload.detail ?? null,
        startedAt: now,
        status: payload.state === "done" ? (payload.status ?? "completed") : null,
        summary: payload.summary ?? null,
      },
    ];
  }

  const prev = rows[index]!;
  const toolChanged = payload.tool !== undefined && payload.tool !== prev.tool;
  const next: AgentRow = {
    ...prev,
    // 라벨은 한 번 정해지면 유지한다: started가 유일한 출처이고, 이후 갱신에는
    // 없다(부재가 "지워라"가 되면 행이 첫 하트비트에 이름을 잃는다).
    label: payload.label ?? prev.label,
    tool: payload.tool ?? prev.tool,
    detail: payload.detail ?? (toolChanged ? null : prev.detail),
    status: payload.state === "done" ? (payload.status ?? "completed") : prev.status,
    summary: payload.summary ?? prev.summary,
  };
  const out = [...rows];
  out[index] = next;
  return out;
}

/** 아직 도는 행만. 고정 줄이 그리는 것이 이것이다. */
export function runningAgents(rows: readonly AgentRow[]): AgentRow[] {
  return rows.filter((r) => r.status === null);
}
