// frontend/lib/liveActivity.ts — 지금 도는 턴의 "가장 마지막에 일어난 일".
//
// 고정 줄(입력창 위)이 읽는 값을 고른다. **목록이 아니라 하나다** — 목록은 턴이
// 끝난 뒤 채팅 버블 아래에 붙는 접힌 진행 기록이 갖는다. 진행 상황을 대화 흐름
// 안에서 계속 갱신하던 종전 방식은 말풍선의 텍스트 스트리밍과 동시에 움직여
// 산만했고, 그것이 이 분리의 이유다.
//
// 화면(워크스페이스·프로토타입 빌드)이 각자 자기 입력창 위에 바를 놓으므로 이
// 함수도 화면이 부른다 — 진행 상황은 **메시지**의 속성이 아니라 **화면**의
// 속성이다.
import type { LiveActivity } from "@/lib/useTurnStream";

/** 이 함수가 필요한 최소 형태.
 *
 *  화면마다 `ChatItem` 유니온이 다르다 — 워크스페이스는 `HistoryCardItem`을,
 *  프로토타입 빌드는 user/ai만, 은퇴한 캔버스는 `CardItem`을 갖는다. 어느 하나를
 *  골라 받으면 나머지 화면이 그 유니온에 맞지 않아 캐스팅이 생기므로, 필요한
 *  필드만 구조로 받는다. */
interface MaybeStreamingAi {
  role: string;
  streaming?: boolean;
  activity?: LiveActivity;
}

/** 도는 턴이 없으면 null(고정 줄을 그리지 않는다). */
export function liveActivity(
  items: readonly MaybeStreamingAi[],
): LiveActivity | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]!;
    if (it.role !== "ai" || !it.streaming) continue;
    // 턴을 보낸 직후부터 첫 프레임까지의 짧은 구간에는 값이 없다. 빈 줄보다
    // 사고가 정확하다 — 모델은 그때 거의 항상 생각하고 있고, 그 구간이 가장
    // 길고 가장 불안한 구간이다.
    return it.activity ?? { kind: "thinking" };
  }
  return null;
}
