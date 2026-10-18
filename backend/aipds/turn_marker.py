# backend/aipds/turn_marker.py — keeps a marker for the running turn in S3.
#
# The turn job (turn_job.py) lives inside the backend process, so it cannot survive a restart. Not surviving
# is itself accepted — but **the fact that it did not survive** must reach the screen.
# Otherwise the user sees an endless "in progress" or an empty bubble with no explanation.
#
# This marker is written when a turn starts and when it ends. If after startup there is no turn in memory but the marker
# is `running`, that turn was cut off by a restart (`GET /turn` reports it as `interrupted`).
# If the terminal write fails and the marker stays `running`, the result is the same — a `running`
# not in memory means, either way, "a turn this process did not see through to the end".
from __future__ import annotations

import json
import logging

from aipds.s3store import S3StoreLike
from aipds.turn_job import TurnJob

_log = logging.getLogger(__name__)

#: The project prefix is added by S3Store. It is outside the workspace restore targets
#: (runner._RESTORE_PREFIXES), so it does not appear in the agent directory.
TURN_MARKER_KEY = "turns/latest.json"


async def save_marker(s3: S3StoreLike, job: TurnJob) -> None:
    await s3.put(TURN_MARKER_KEY, json.dumps(
        {"turn_id": job.id, "kind": job.kind, "state": job.state}))


async def load_marker(s3: S3StoreLike) -> dict | None:
    """None if missing or corrupted — the marker is informational, and missing means the same as "no turn"."""
    try:
        raw = await s3.get(TURN_MARKER_KEY)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("turn marker is not valid JSON — ignoring")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("turn_id"), str):
        return None
    return data
