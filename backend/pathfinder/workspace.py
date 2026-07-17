# backend/pathfinder/workspace.py
from __future__ import annotations
from pathfinder.sandbox.base import Sandbox
from pathfinder.models import QuestionFile, ProjectState, AuditEntry
from pathfinder.parsers.questions import parse_question_file, serialize_answers
from pathfinder.parsers.state import parse_state_file
from pathfinder.parsers.audit import parse_audit_file

_DOC_PATH = "aiplc-docs/discovery/discovery-document.md"
_STATE_PATH = "aiplc-docs/aiplc-state.md"
_AUDIT_PATH = "aiplc-docs/audit.md"

class Workspace:
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    async def get_questions(self, name: str) -> QuestionFile:
        md = await self.sandbox.read_file(name)
        return parse_question_file(name.split("/")[-1], md)

    async def put_answers(self, name: str, answers: dict[int, str]) -> QuestionFile:
        md = await self.sandbox.read_file(name)
        new_md = serialize_answers(md, answers)
        await self.sandbox.write_file(name, new_md)
        return parse_question_file(name.split("/")[-1], new_md)

    async def get_state(self) -> ProjectState:
        try:
            md = await self.sandbox.read_file(_STATE_PATH)
        except FileNotFoundError:
            return ProjectState(stages=[])
        return parse_state_file(md)

    async def get_audit(self) -> list[AuditEntry]:
        try:
            md = await self.sandbox.read_file(_AUDIT_PATH)
        except FileNotFoundError:
            return []
        return parse_audit_file(md)

    async def get_document(self) -> str:
        try:
            return await self.sandbox.read_file(_DOC_PATH)
        except FileNotFoundError:
            return ""

    async def list_question_files(self) -> list[str]:
        return await self.sandbox.list_files("aiplc-docs/**/*-questions.md")

class ProjectRegistry:
    def __init__(self):
        self._projects: dict[str, Workspace] = {}

    def create(self, project_id: str, sandbox: Sandbox) -> Workspace:
        ws = Workspace(sandbox)
        self._projects[project_id] = ws
        return ws

    def get(self, project_id: str) -> Workspace:
        return self._projects[project_id]
