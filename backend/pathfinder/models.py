from __future__ import annotations
from typing import Literal
from pydantic import BaseModel

class QuestionOption(BaseModel):
    letter: str
    text: str
    is_other: bool = False
    recommended: bool = False

class Question(BaseModel):
    number: int
    category: str | None = None
    #: 옵션 앞의 본문 전체(배경 산문 포함). 화면에 보여주는 값이다.
    text: str
    #: 그중 **마지막 문단** — 실제로 묻는 문장이다.
    #:
    #: 왜 나눠 두는가: 파일은 배경 산문 + 질문을 함께 담는데 AskUserQuestion에는
    #: 질문 문장만 간다. 답변 되기록이 `text`(~200자)와 도구의 질문(~22자)을
    #: 비교하면 길이 차이로 유사도가 무너진다 — 2026-08-16 keumkang-v5의
    #: design-context.md Q4가 그렇게 유실됐다(0.3721). 문단이 하나면 text와 같다.
    ask: str = ""
    options: list[QuestionOption]
    answer: str | None = None
    multi_select: bool = False

class QuestionFile(BaseModel):
    name: str
    preamble: str | None = None
    questions: list[Question]
    parse_ok: bool
    raw_markdown: str | None = None

class StageState(BaseModel):
    name: str
    status: Literal["pending", "in_progress", "completed"]
    note: str | None = None

class ProjectState(BaseModel):
    project_type: str | None = None
    current_stage: str | None = None
    stages: list[StageState]

class AuditEntry(BaseModel):
    index: int
    timestamp: str
    user_input: str
    ai_response: str
    context: str | None = None

class HistoryTraceEntry(BaseModel):
    """라이브 AgentEvent의 status/file_changed에 대응하는 복원용 트레이스 —
    프론트 AiMessage의 "추론 과정" 아코디언이 소비하는 최소 shape."""
    kind: Literal["status", "file_changed"]
    text: str | None = None
    path: str | None = None


class HistoryItem(BaseModel):
    role: Literal["user", "ai", "card"]
    text: str | None = None
    card: Literal["questions"] | None = None
    name: str | None = None
    # role=="ai"일 때 그 턴의 도구 실행 트레이스(없으면 빈 리스트)
    trace: list[HistoryTraceEntry] = []
    # 답변 제출 턴의 구조화된 답변({"1": "A", "2": "B,C"}). 사람이 읽는 문구는
    # 프론트가 UI 언어로 만든다 — 백엔드는 UI 언어를 모른다. JSON이 아닌 자유
    # 서술 답변은 dict로 펼 수 없어 None이고, 그때는 text만 쓴다.
    answers: dict[str, str] | None = None
    # 그 라운드의 질문 payload(QuestionFile). 있으면 프론트가 라이브와 같은
    # answerSummary()로 문구를 만든다 — 문항 번호·보기 letter·보기 텍스트가
    # 여기서 나온다. role=="card"에도 실어 "무엇을 물었는지"를 복원한다.
    questions: dict | None = None

class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error",
                  # Discovery가 프로토타입을 빌드로 넘겼다는 선언
                  # (agent/tools.py의 handoff_prototype). 프론트가 이 이벤트로
                  # "Prototypes 탭으로 가기" 카드를 그린다 — 에이전트가 안내
                  # 문장을 잊어도 사용자에게 클릭할 곳이 남아야 한다.
                  "prototype_ready",
                  # 프로토타입 빌드의 명시적 완료 선언(proto/tools.py). 이
                  # 이벤트가 세션의 수명을 끝낸다 — proto/session.py가
                  # 관찰해 status를 "complete"로 바꾼다.
                  "build_complete"]
    text: str | None = None
    path: str | None = None
    # Structured payload (JSON string) for questions/stage/document — the
    # event IS the UI contract; files stay as records only.
    payload: str | None = None

class TurnResult(BaseModel):
    events: list[AgentEvent]
