# backend/aipds/workspace.py
from __future__ import annotations
from aipds.models import QuestionFile, ProjectState, AuditEntry
from aipds.parsers.questions import parse_question_file, serialize_answers
from aipds.parsers.state import parse_state_file
from aipds.parsers.audit import parse_audit_file

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
        # "Artifact" = every file under aiplc-docs/: the dashboard's artifacts
        # panel and Phase 1's file-as-contract model both treat the whole aiplc-docs/
        # subtree as project output, not just *.md. Glob mirrors
        # list_question_files's use of runner.list_files (same traversal guard,
        # no new IO path). runner.list_files already filters to files (not
        # directories), so `**/*` matched directories are excluded automatically.
        #
        # **Newest first (2026-08-21).** The workspace document viewer falls back
        # to the first entry of this list when it has no `activeDoc`, so the order
        # carries meaning. Alphabetical put `aiplc-docs/audit.md` first almost
        # every time, which opened the audit log instead of the document the
        # conversation had just produced.
        return await self.runner.list_files_newest_first("aiplc-docs/**/*")

class ProjectRegistry:
    """Keeps "projects we know of" (_names) apart from "live workspaces"
    (_workspaces).

    A project restored from the S3 manifest starts out registered only -- it
    appears in the list but has no workspace -- and `deps.ensure_workspace`
    attaches one on the first request that needs it."""

    def __init__(self):
        self._names: dict[str, str | None] = {}
        self._workspaces: dict[str, Workspace] = {}
        # Creation time (ISO string) -- the sort key for the project list. It
        # arrives either from the manifest or from the create route. Older
        # manifests may not have it, hence None is allowed.
        self._created_at: dict[str, str | None] = {}
        # The Bedrock model id this project runs on. It is a COPY of the value,
        # not a foreign key into the catalog: an admin deleting a model from the
        # catalog must not take the model away from a project already in
        # progress. None = unset (falls back to env).
        self._model_id: dict[str, str | None] = {}
        # The output language for this project ("ko"|"en"). Copied from the
        # manifest under the same discipline as model_id. None = unset (which
        # includes older manifests) -- `get_language` settles it to "ko".
        self._language: dict[str, str | None] = {}

    def register(self, project_id: str, name: str | None = None,
                 created_at: str | None = None,
                 model_id: str | None = None,
                 language: str | None = None) -> None:
        self._names[project_id] = name
        self._created_at[project_id] = created_at
        self._model_id[project_id] = model_id
        self._language[project_id] = language

    def attach(self, project_id: str, workspace: Workspace) -> Workspace:
        if project_id not in self._names:
            # No attaching without registering first -- this surfaces call-order
            # bugs immediately instead of leaving an orphan workspace behind.
            raise KeyError(project_id)
        self._workspaces[project_id] = workspace
        return workspace

    def get(self, project_id: str) -> Workspace:
        return self._workspaces[project_id]

    def is_registered(self, project_id: str) -> bool:
        return project_id in self._names

    def has_workspace(self, project_id: str) -> bool:
        return project_id in self._workspaces

    def remove(self, project_id: str) -> Workspace | None:
        """Drop both the registration and the workspace. Returns the Workspace
        that was there (None if none). Idempotent."""
        self._names.pop(project_id, None)
        self._created_at.pop(project_id, None)
        self._model_id.pop(project_id, None)
        self._language.pop(project_id, None)
        return self._workspaces.pop(project_id, None)

    def list_ids(self) -> list[str]:
        # Ascending by creation date (oldest first). Projects from older
        # manifests, which have no created_at, sort to the front. An ISO string
        # compares lexicographically the same way it compares chronologically,
        # so a plain str comparison is enough.
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

        Unlike `get_name`, an unregistered project is not a KeyError:
        `app.project_model()` uses this as the first slot of a fallback chain, so
        treating "unregistered" as "no model" keeps the caller simple.
        """
        return self._model_id.get(project_id)

    #: The permitted output languages. `place_rules` selects the per-language
    #: directive block by this value, so nothing else can exist.
    _LANGUAGES = ("ko", "en")

    def get_language(self, project_id: str) -> str:
        """This project's output language. **Always returns "ko" or "en".**

        A deliberate contrast with `get_model_id`, which returns None: there is
        no valid "no language" state -- a document has to be written in SOME
        language. If every caller (place_rules, the prototype prompts, the survey
        report) repeated its own fallback, the one that forgot would quietly
        produce a different language, so this settles it in one place.

        The fallback is "ko" because every project created before this feature
        existed was written in Korean -- older manifests have no language key.

        An unknown value also lands on "ko". The create route validates the
        value, so nothing else can arrive through the normal path; but if a
        corrupted manifest carries an arbitrary string, running in Korean beats
        raising.
        """
        value = self._language.get(project_id)
        return value if value in self._LANGUAGES else "ko"
