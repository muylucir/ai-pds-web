# harness/events.py — mirror of backend/pathfinder/sandbox/base.py AgentEvent.
# Fields MUST stay identical (kind/text/path/payload) or the SSE contract breaks.
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel

class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None
    payload: str | None = None
