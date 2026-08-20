# backend/pathfinder/workspace.py
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
        # 생성 시각(ISO 문자열) — 목록 정렬 기준. 매니페스트에서 복원되거나
        # 생성 라우트가 전달한다. 구 매니페스트에는 없을 수 있어 None 허용.
        self._created_at: dict[str, str | None] = {}
        # 이 프로젝트가 도는 Bedrock 모델 id. 카탈로그를 참조(FK)하지 않고
        # 값을 복사해 둔 것이다 — 관리자가 모델을 카탈로그에서 지워도 진행
        # 중인 프로젝트가 모델을 잃으면 안 된다. None = 미지정(env 폴백).
        self._model_id: dict[str, str | None] = {}
        # 이 프로젝트의 생성물 언어("ko"|"en"). model_id와 같은 규율로
        # 매니페스트에서 복사돼 온다. None = 미지정(구 매니페스트 포함) —
        # get_language가 "ko"로 확정한다.
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
        self._created_at.pop(project_id, None)
        self._model_id.pop(project_id, None)
        self._language.pop(project_id, None)
        return self._workspaces.pop(project_id, None)

    def list_ids(self) -> list[str]:
        # 생성일 오름차순(오래된 것 먼저). created_at이 없는 구 매니페스트
        # 프로젝트는 맨 앞 — ISO 문자열은 사전순 == 시간순이라 str 비교로 충분.
        return sorted(self._names.keys(),
                      key=lambda pid: self._created_at.get(pid) or "")

    def get_name(self, project_id: str) -> str | None:
        if project_id not in self._names:
            raise KeyError(project_id)
        return self._names[project_id]

    def get_created_at(self, project_id: str) -> str | None:
        return self._created_at.get(project_id)

    def get_model_id(self, project_id: str) -> str | None:
        """이 프로젝트의 모델 id, 없으면 None.

        get_name과 달리 미등록에 KeyError를 내지 않는다 —
        app.project_model()이 폴백 체인의 첫 칸으로 쓰므로, 미등록도
        '모델 없음'으로 다루는 것이 호출부를 단순하게 만든다.
        """
        return self._model_id.get(project_id)

    #: 생성물 언어의 허용값. place_rules가 이 값으로 언어별 지시 블록을 고르므로
    #: 그 밖의 값은 존재할 수 없다.
    _LANGUAGES = ("ko", "en")

    def get_language(self, project_id: str) -> str:
        """이 프로젝트의 생성물 언어. **항상 "ko" 또는 "en"을 돌려준다.**

        get_model_id가 None을 돌려주는 것과 다른 선택이다: 언어에는 "없음"이라는
        유효 상태가 없다 — 문서는 어떤 언어로든 써야 한다. 호출부(place_rules,
        프로토타입 프롬프트, 설문 리포트)가 각자 폴백을 반복하면 그중 하나가
        빠뜨렸을 때 조용히 다른 언어가 나오므로, 여기서 확정한다.

        폴백이 "ko"인 이유는 이 기능 이전에 만든 프로젝트가 전부 한국어로
        만들어졌기 때문이다 — 구 매니페스트에는 language 키가 없다.

        알 수 없는 값도 "ko"로 떨어진다. 라우트가 생성 시점에 검증하므로
        정상 경로로는 들어올 수 없지만, 손상된 매니페스트가 임의 문자열을
        실어 오면 던지는 것보다 한국어로 도는 편이 낫다.
        """
        value = self._language.get(project_id)
        return value if value in self._LANGUAGES else "ko"
