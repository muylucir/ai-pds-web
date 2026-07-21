# backend/pathfinder/workspace.py
from __future__ import annotations
from pathfinder.models import QuestionFile, ProjectState, AuditEntry
from pathfinder.parsers.questions import parse_question_file, serialize_answers
from pathfinder.parsers.state import parse_state_file
from pathfinder.parsers.audit import parse_audit_file

_DOC_PATH = "aiplc-docs/discovery/discovery-document.md"
_STATE_PATH = "aiplc-docs/aiplc-state.md"
_AUDIT_PATH = "aiplc-docs/audit.md"

class Workspace:
    def __init__(self, runner):
        self.runner = runner

    async def get_questions(self, name: str) -> QuestionFile:
        md = await self.runner.read_file(name)
        return parse_question_file(name.split("/")[-1], md)

    async def put_answers(self, name: str, answers: dict[int, str]) -> QuestionFile:
        md = await self.runner.read_file(name)
        new_md = serialize_answers(md, answers)
        await self.runner.write_file(name, new_md)
        return parse_question_file(name.split("/")[-1], new_md)

    async def get_state(self) -> ProjectState:
        try:
            md = await self.runner.read_file(_STATE_PATH)
        except FileNotFoundError:
            return ProjectState(stages=[])
        return parse_state_file(md)

    async def get_audit(self) -> list[AuditEntry]:
        try:
            md = await self.runner.read_file(_AUDIT_PATH)
        except FileNotFoundError:
            return []
        return parse_audit_file(md)

    async def get_document(self) -> str:
        try:
            return await self.runner.read_file(_DOC_PATH)
        except FileNotFoundError:
            return ""

    async def list_question_files(self) -> list[str]:
        return await self.runner.list_files("aiplc-docs/**/*-questions.md")

    async def list_artifacts(self) -> list[str]:
        # "Artifact" = every file under aiplc-docs/: the dashboard's 산출물 panel
        # and Phase 1's file-as-contract model both treat the whole aiplc-docs/
        # subtree as project output, not just *.md. Glob mirrors
        # list_question_files's use of runner.list_files (same traversal guard,
        # no new IO path). runner.list_files already filters to files (not
        # directories), so `**/*` matched directories are excluded automatically.
        return await self.runner.list_files("aiplc-docs/**/*")

class ProjectRegistry:
    """'아는 프로젝트'(_names)와 '살아있는 워크스페이스'(_workspaces)를 분리.

    S3 매니페스트에서 복원된 프로젝트는 register만 된 상태(목록에는 보이지만
    워크스페이스 없음)로 시작하고, 첫 요청 시 deps.ensure_workspace가 attach한다."""

    def __init__(self):
        self._names: dict[str, str | None] = {}
        self._workspaces: dict[str, Workspace] = {}

    def register(self, project_id: str, name: str | None = None) -> None:
        self._names[project_id] = name

    def attach(self, project_id: str, workspace: Workspace) -> Workspace:
        if project_id not in self._names:
            raise KeyError(project_id)  # 등록 없이 연결 금지 — 호출 순서 버그를 조기 검출
        self._workspaces[project_id] = workspace
        return workspace

    def get(self, project_id: str) -> Workspace:
        return self._workspaces[project_id]

    def is_registered(self, project_id: str) -> bool:
        return project_id in self._names

    def has_workspace(self, project_id: str) -> bool:
        return project_id in self._workspaces

    def remove(self, project_id: str) -> Workspace | None:
        """등록·워크스페이스 모두 제거. 있던 Workspace를 반환(없으면 None). 멱등."""
        self._names.pop(project_id, None)
        return self._workspaces.pop(project_id, None)

    def list_ids(self) -> list[str]:
        # dict는 삽입 순서를 보존 — 등록(생성/복원) 순서 그대로 노출
        return list(self._names.keys())

    def get_name(self, project_id: str) -> str | None:
        if project_id not in self._names:
            raise KeyError(project_id)
        return self._names[project_id]
