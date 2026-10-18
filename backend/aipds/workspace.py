# backend/aipds/workspace.py
from __future__ import annotations
from aipds.models import QuestionFile, ProjectState, AuditEntry
from aipds.parsers.questions import parse_question_file, serialize_answers
from aipds.parsers.state import parse_state_file
from aipds.parsers.audit import parse_audit_file
from aipds.turn_job import TurnJobs

_STATE_PATH = "aiplc-docs/aiplc-state.md"
_AUDIT_PATH = "aiplc-docs/audit.md"

class Workspace:
    def __init__(self, runner, turns: TurnJobs | None = None):
        self.runner = runner
        #: This project's turn job. A turn belongs here, not to a request (turn_job.py header).
        self.turns = turns or TurnJobs()

    async def stop(self) -> None:
        """Cuts the running turn first, then stops the runner (project deletion path).

        The order is the point: if the runner took the driver down first, the running turn would wait on
        a dead subprocess and end in an error. Cancelling the turn first closes that generator on the normal path.
        """
        await self.turns.cancel()
        await self.runner.stop()

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

    async def list_question_files(self) -> list[str]:
        return await self.runner.list_files("aiplc-docs/**/*-questions.md")

    async def list_artifacts(self) -> list[str]:
        # "Artifact" = every file under aiplc-docs/: the dashboard's 산출물 panel
        # and Phase 1's file-as-contract model both treat the whole aiplc-docs/
        # subtree as project output, not just *.md. Glob mirrors
        # list_question_files's use of runner.list_files (same traversal guard,
        # no new IO path). runner.list_files already filters to files (not
        # directories), so `**/*` matched directories are excluded automatically.
        #
        # **Newest first (2026-08-21).** The workspace document viewer falls back to this list's first
        # item when there is no `activeDoc`, so the order carries meaning. In alphabetical order that
        # first item is almost always `aiplc-docs/audit.md`, and the audit log opens instead of the
        # document the conversation just created.
        return await self.runner.list_files_newest_first("aiplc-docs/**/*")

class ProjectRegistry:
    """Separates 'known projects' (_names) from 'live workspaces' (_workspaces).

    A project restored from the S3 manifest starts in a register-only state (visible in the list but
    without a workspace), and deps.ensure_workspace attaches it on the first request."""

    def __init__(self):
        self._names: dict[str, str | None] = {}
        self._workspaces: dict[str, Workspace] = {}
        # Creation time (ISO string) — the list sort key. Restored from the manifest or
        # passed in by the create route. Old manifests may lack it, so None is allowed.
        self._created_at: dict[str, str | None] = {}
        # The Bedrock model id this project runs on. It is a copied value, not a reference (FK)
        # to the catalog — even if an admin deletes the model from the catalog, an in-progress
        # project must not lose its model. None = unspecified (env fallback).
        self._model_id: dict[str, str | None] = {}
        # This project's output language ("ko"|"en"). Copied from the manifest under the
        # same discipline as model_id. None = unspecified (including old manifests) —
        # get_language settles it as "ko".
        self._language: dict[str, str | None] = {}
        # This project's effort. Copied from the catalog at creation time, paired with model_id.
        # None = CLI default.
        self._effort: dict[str, str | None] = {}
        # Cognito usernames of the owner and viewing members (normalized values, project_access.normalize).
        # The source of truth is the manifest and this is its copy — whoever changes it writes the manifest first
        # (project_access.set_access).
        self._owner: dict[str, str] = {}
        self._members: dict[str, tuple[str, ...]] = {}

    def register(self, project_id: str, name: str | None = None,
                 created_at: str | None = None,
                 model_id: str | None = None,
                 language: str | None = None,
                 effort: str | None = None,
                 owner: str | None = None,
                 members: tuple[str, ...] = ()) -> None:
        """If there is no owner, registers it as owned by the seed admin. Manifests from before the
        owner was recorded come in through this path — that interpretation lives in this one place
        so that restore, import, and tests do not each do it themselves."""
        from aipds.project_access import SEED_ADMIN
        self._names[project_id] = name
        self._created_at[project_id] = created_at
        self._model_id[project_id] = model_id
        self._language[project_id] = language
        self._effort[project_id] = effort
        self.set_access(project_id, owner or SEED_ADMIN, members)

    def set_access(self, project_id: str, owner: str, members: tuple[str, ...]) -> None:
        from aipds.project_access import clean_access
        self._owner[project_id], self._members[project_id] = clean_access(owner, members)

    def get_owner(self, project_id: str) -> str | None:
        return self._owner.get(project_id)

    def get_members(self, project_id: str) -> tuple[str, ...]:
        return self._members.get(project_id, ())

    def attach(self, project_id: str, workspace: Workspace) -> Workspace:
        if project_id not in self._names:
            raise KeyError(project_id)  # no attach without registration — catches call-order bugs early
        self._workspaces[project_id] = workspace
        return workspace

    def get(self, project_id: str) -> Workspace:
        return self._workspaces[project_id]

    def is_registered(self, project_id: str) -> bool:
        return project_id in self._names

    def has_workspace(self, project_id: str) -> bool:
        return project_id in self._workspaces

    def remove(self, project_id: str) -> Workspace | None:
        """Removes both the registration and the workspace. Returns the Workspace that existed (None if none). Idempotent."""
        self._names.pop(project_id, None)
        self._created_at.pop(project_id, None)
        self._model_id.pop(project_id, None)
        self._language.pop(project_id, None)
        self._effort.pop(project_id, None)
        self._owner.pop(project_id, None)
        self._members.pop(project_id, None)
        return self._workspaces.pop(project_id, None)

    def list_ids(self) -> list[str]:
        # Ascending by creation date (oldest first). Projects from old manifests without created_at
        # go first — ISO strings sort lexicographically == chronologically, so str comparison suffices.
        return sorted(self._names.keys(),
                      key=lambda pid: self._created_at.get(pid) or "")

    def get_name(self, project_id: str) -> str | None:
        if project_id not in self._names:
            raise KeyError(project_id)
        return self._names[project_id]

    def get_created_at(self, project_id: str) -> str | None:
        return self._created_at.get(project_id)

    def get_model_id(self, project_id: str) -> str | None:
        """This project's model id, or None.

        Unlike get_name, it does not raise KeyError for unregistered projects —
        app.project_model() uses it as the first slot in the fallback chain, so treating
        unregistered as 'no model' keeps the callers simple.
        """
        return self._model_id.get(project_id)

    def get_effort(self, project_id: str) -> str | None:
        """This project's effort, or None (CLI default).

        Unknown values are also None — if a corrupted manifest carries an arbitrary string, the
        CLI rejects `--effort` and the turn does not start. Running on the default is better.
        """
        from aipds.model_catalog import EFFORTS
        value = self._effort.get(project_id)
        return value if value in EFFORTS else None

    #: Allowed values for the output language. place_rules picks the per-language directive block by this value,
    #: so no other value can exist.
    _LANGUAGES = ("ko", "en")

    def get_language(self, project_id: str) -> str:
        """This project's output language. **Always returns "ko" or "en".**

        This is a different choice from get_model_id returning None: language has no valid "none"
        state — documents must be written in some language. If callers (place_rules, prototype
        prompts, survey reports) each repeated the fallback, one of them forgetting it would silently
        produce a different language, so it is settled here.

        The fallback is "ko" because every project created before this feature was created in
        Korean — old manifests have no language key.

        Unknown values also fall back to "ko". The route validates at creation time, so they cannot
        come in through the normal path, but if a corrupted manifest carries an arbitrary string,
        running in Korean is better than raising.
        """
        value = self._language.get(project_id)
        return value if value in self._LANGUAGES else "ko"
