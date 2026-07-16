# backend/pathfinder/parsers/state.py
from __future__ import annotations
import re
from pathfinder.models import ProjectState, StageState

_PROJECT_TYPE = re.compile(r"\*\*Project Type\*\*:\s*(.+)")
_CURRENT_STAGE = re.compile(r"\*\*Current Stage\*\*:\s*(.+)")
_CHECK = re.compile(r"^- \[([ xX])\]\s*(.+)$")
_SPLIT = re.compile(r"\s+[—-]\s+")

def parse_state_file(markdown: str) -> ProjectState:
    project_type = None
    current_stage = None
    stages: list[StageState] = []
    for line in markdown.splitlines():
        line = line.rstrip()
        if project_type is None and (m := _PROJECT_TYPE.search(line)):
            project_type = m.group(1).strip()
            continue
        if current_stage is None and (m := _CURRENT_STAGE.search(line)):
            current_stage = m.group(1).strip()
            continue
        if (m := _CHECK.match(line.strip())):
            checked = m.group(1).lower() == "x"
            body = m.group(2).strip()
            parts = _SPLIT.split(body, maxsplit=1)
            name = parts[0].strip()
            note = parts[1].strip() if len(parts) > 1 else None
            status = "completed" if checked else "pending"
            stages.append(StageState(name=name, status=status, note=note))

    if current_stage:
        pending = [s for s in stages if s.status == "pending"]
        exact = [s for s in pending if s.name == current_stage]
        if exact:
            exact[0].status = "in_progress"
        else:
            partial = [
                s for s in pending
                if s.name in current_stage or current_stage in s.name
            ]
            if partial:
                best = max(partial, key=lambda s: len(s.name))
                best.status = "in_progress"

    return ProjectState(project_type=project_type, current_stage=current_stage, stages=stages)
