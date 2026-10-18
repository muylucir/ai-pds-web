# backend/aipds/context_usage.py — the agent's context window usage (the "% left" on screen).
#
# The value comes from the CLI itself: `ClaudeSDKClient.get_context_usage()` returns the
# same thing as the CLI's `/context`. We do not divide the token count by the model's
# window size because in this deployment that division is wrong — the window becomes
# 1 million via the `[1m]` suffix (cli_settings), and `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
# moves the auto-compaction point. The CLI knows both and does the math.
#
# Measured (2026-09-29, SDK 0.2.157, Bedrock Haiku 4.5):
#   - The first call takes ~650ms (it appears to estimate via the API); after one turn
#     has run it takes 4-5ms — it uses the usage from the last API response (the
#     input+cache sum matched totalTokens).
#   - Calling it while a turn is running (during tool execution) does not break the turn.
#   - With the auto-compaction window set to 100000, maxTokens=100000 and categories
#     carries a separate "Autocompact buffer" (33000). With default settings that entry
#     was absent.
# So "% left" is the share left **until compaction**: the fraction of usable space
# (maxTokens − compaction buffer) that is still empty. 0% is exactly where compaction
# happens.
#
# The driver makes the call (on every main-agent response — a build turn lasts tens of
# minutes, so measuring only at the end of the turn would freeze the display meanwhile).
# The runner/session writes the last value to S3 when a turn ends (`ContextRecord`) —
# that is the value the screen shows when no client is connected (after the session
# closes, after a backend restart).
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aipds.models import AgentEvent
from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: Time allowed for one probe. The first probe took ~650ms — generous beyond that, but
#: short enough that this probe does not visibly stall the turn.
_PROBE_TIMEOUT_SECONDS = 3.0

_AUTOCOMPACT_BUFFER = "Autocompact buffer"

#: Discovery's record (project-relative). It sits next to the transcript
#: (discovery/transcript/) — project deletion removes it along with it, and a bundle
#: carries it together with the active transcript. Since the conversation moves, it
#: is right for that conversation's usage to move with it.
DISCOVERY_KEY = "discovery/context.json"


def wants_sample(msg: Any) -> bool:
    """Whether to measure usage after this message: main-agent responses and the end of a turn.

    Subagent messages (which have a parent_tool_use_id) run in **their own** context —
    auto-compaction happens on the main side, and that is also what the screen shows.
    """
    tname = type(msg).__name__
    if tname == "ResultMessage":
        return True
    return tname == "AssistantMessage" and not getattr(msg, "parent_tool_use_id", None)


def summarize(resp: dict) -> dict | None:
    """`get_context_usage()` response → screen payload. None if the shape differs."""
    try:
        total = int(resp["totalTokens"])
        maximum = int(resp["maxTokens"])
        buffer = sum(int(c.get("tokens", 0)) for c in resp.get("categories", [])
                     if c.get("name") == _AUTOCOMPACT_BUFFER)
    except (KeyError, TypeError, ValueError):
        return None
    usable = maximum - buffer
    if maximum <= 0 or usable <= 0:
        return None
    left = max(0, usable - total)
    return {
        "total_tokens": total,
        "max_tokens": maximum,
        # The usage at which compaction happens. With no buffer, it is the end of the window.
        "compact_at_tokens": usable,
        "left_pct": min(100, left * 100 // usable),
    }


class ContextMeter:
    """The meter for one driver. Emits an event only when the value changes.

    A separate AssistantMessage arrives for each block of the same API response (one per
    text, one per tool call), so emitting every time would send the same value dozens of
    times. The comparison uses the integer the screen shows (`left_pct`) — comparing down
    to the token count would differ almost every time and filter out nothing.
    """

    def __init__(self) -> None:
        self._shown: int | None = None
        self._warned = False

    async def sample(self, client: Any) -> AgentEvent | None:
        probe = getattr(client, "get_context_usage", None)
        if probe is None:
            return None  # an SDK or test double without this feature — there is simply no display
        try:
            resp = await asyncio.wait_for(probe(), _PROBE_TIMEOUT_SECONDS)
        except Exception:
            # This value is display-only. Do not block the turn, and log only once — if it
            # fails on every response, a single build would produce hundreds of lines.
            if not self._warned:
                self._warned = True
                _log.warning("context usage probe failed", exc_info=True)
            return None
        payload = summarize(resp) if isinstance(resp, dict) else None
        if payload is None or payload["left_pct"] == self._shown:
            return None
        self._shown = payload["left_pct"]
        return AgentEvent(kind="context",
                          payload=json.dumps(payload, ensure_ascii=False))


class ContextRecord:
    """Records a turn's last usage in S3. Used by the runner (Discovery) and build sessions.

    `observe` takes the event stream as is, and `flush` writes only a changed value when
    the turn ends. `tag` is stored with the record so the reader can judge "is this still
    valid?" — prototypes store the SDK session id (an improvement session is a new
    conversation, so the previous value is wrong).
    """

    def __init__(self, s3: S3StoreLike, key: str) -> None:
        self._s3 = s3
        self._key = key
        self._latest: dict | None = None
        self._dirty = False

    def observe(self, event: AgentEvent) -> None:
        if event.kind != "context" or not event.payload:
            return
        try:
            self._latest = json.loads(event.payload)
        except json.JSONDecodeError:
            return
        self._dirty = True

    async def flush(self, **tag: str | None) -> None:
        """Swallows failures — a display-only value must not block the turn's completion (sync, done)."""
        if not self._dirty or self._latest is None:
            return
        self._dirty = False
        try:
            await self._s3.put(self._key, json.dumps({**self._latest, **tag},
                                                     ensure_ascii=False))
        except Exception:
            _log.exception("context usage record failed: %s", self._key)


async def load_record(s3: S3StoreLike, key: str) -> dict | None:
    """The last recorded usage. None if missing or unreadable."""
    try:
        data = json.loads(await s3.get(key))
    except FileNotFoundError:
        return None
    except Exception:
        _log.warning("unreadable context usage record: %s", key, exc_info=True)
        return None
    return data if isinstance(data, dict) else None
