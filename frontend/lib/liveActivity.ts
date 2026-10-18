// frontend/lib/liveActivity.ts — the "most recent thing that happened" in the running turn.
//
// Picks the value the pinned line (above the input) reads. **It is one item, not a list** — the
// list belongs to the collapsed progress log attached under the chat bubble after the turn
// ends. The earlier approach, which kept updating progress inside the conversation flow, moved
// at the same time as the bubble's text streaming and was distracting; that is the reason for this split.
//
// Each screen (workspace, prototype build) places the bar above its own input, so the screen
// calls this function too — progress is a property of the **screen**, not of the
// **message**.
import type { LiveActivity } from "@/lib/chatItems";

/** The minimal shape this function needs.
 *
 *  Each screen has a different `ChatItem` union — the workspace has `HistoryCardItem`,
 *  the prototype build has only user/ai, and the retired canvas has `CardItem`. Accepting one
 *  of them would force casts on the other screens whose unions do not match, so it accepts
 *  only the needed fields structurally. */
interface MaybeStreamingAi {
  role: string;
  streaming?: boolean;
  activity?: LiveActivity;
}

/** null when no turn is running (the pinned line is not drawn). */
export function liveActivity(
  items: readonly MaybeStreamingAi[],
): LiveActivity | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]!;
    if (it.role !== "ai" || !it.streaming) continue;
    // There is no value in the short interval right after the turn is sent until the first frame.
    // Thinking is more accurate than an empty line — the model is almost always thinking then, and
    // that interval is the longest and most anxious one.
    return it.activity ?? { kind: "thinking" };
  }
  return null;
}
