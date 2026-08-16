# backend/pathfinder/agent/prompts.py — Discovery 에이전트가 **읽는** 텍스트의
# 언어별 두 벌.
#
# 여기 있는 것은 전부 모델 컨텍스트에 들어간다: MCP 도구 설명(매 턴 도구 목록과
# 함께 주입된다), 도구가 돌려주는 거부/확인 문자열, 그리고 드라이버가 만드는
# 프롬프트. 화면 문구가 아니다 — UI 문자열은 frontend/lib/i18n이 소유한다.
#
# **왜 이 파일이 생겼는가(2026-08-04의 결함).** 스펙
# `2026-08-03-bilingual-ko-en` §3은 "언어 지시의 단일 출처"를 `place_rules`로
# 만들었고 그 부분은 정확히 동작했다(en 조립물의 한글 0자). 그런데 영어를 고른
# 프로젝트의 워크스페이스 채팅이 계속 한국어로 진행됐다. 남은 경로가
# 이것들이었기 때문이다 — 도구 설명과 도구 반환값은 **지시가 아니라서** 그
# 스펙의 시야에 없었지만, 모델에게는 똑같이 매 턴 읽는 한국어 프롬프트다.
#
# 그래서 이 리포의 언어 판정 기준은 "언어 지시가 하나인가"가 아니라
# **"모델 컨텍스트에 들어가는 모든 텍스트가 프로젝트 언어인가"**다.
#
# **조립하지 않고 두 벌을 완성문으로 유지한다.** `proto/prompts.py`가 같은
# 판단을 기록해 뒀다: 문장을 조각으로 쪼개 치환하면 어느 언어에서 지시의 강도가
# 약해졌는지 알 수 없게 된다. 여기서는 특히 submit_document의 "먼저 파일을 쓴
# 뒤 호출한다"는 순서 지시가 그렇다 — 그 지시가 약해지면 도구가 거짓 선언을
# 받아들이고 문서 패널이 빈 화면을 보여준다.
from __future__ import annotations

#: 지원 언어. workspace_rules._LANGUAGES와 같은 집합이어야 한다.
_LANGUAGES = ("ko", "en")
_DEFAULT = "ko"


def _lang(language: str) -> str:
    """알 수 없는 값은 기본값으로 떨어진다 — 라우트가 생성 시점에 검증하므로
    정상 경로로는 들어올 수 없고, 손상된 매니페스트 때문에 프롬프트 없이 도는
    것보다 한국어로 도는 편이 낫다(place_rules와 같은 규율)."""
    return language if language in _LANGUAGES else _DEFAULT


# ---- MCP 도구 설명 (agent/tools.py) ----


def report_stage_description(language: str) -> str:
    """`report_stage` 도구 설명. 도구 설명은 모델이 읽는 프롬프트다."""
    if _lang(language) == "en":
        return ("Declare a Discovery stage transition. This also updates "
                "aiplc-state.md automatically.")
    return "Discovery 스테이지 전이를 선언한다. aiplc-state.md도 자동 갱신된다."


def submit_document_description(language: str) -> str:
    """`submit_document` 도구 설명.

    순서 지시("먼저 쓴 뒤 호출")가 이 문장의 핵심이다 — 약해지면 도구가 없는
    문서를 선언하려 하고, 사용자는 "생성됐습니다"를 읽으면서 빈 문서 패널을
    본다.
    """
    if _lang(language) == "en":
        return ("Declare that a document is ready for review, or has been "
                "updated. **You must write the file with Write/Edit FIRST**, "
                "then call this — the declaration is refused if the file is "
                "missing or empty.")
    return ("리뷰 대상 문서가 준비/갱신되었음을 선언한다. **먼저 Write/Edit로 "
            "파일을 쓴 뒤** 호출해야 한다 — 파일이 없거나 비어 있으면 선언이 "
            "거부된다.")


# ---- 도구 반환 문자열 (agent/tools.py) ----
#
# 거부 문자열은 에이전트가 읽고 스스로 고치는 지시다. 무엇이 잘못됐는지와
# 다음에 무엇을 할지를 둘 다 담아야 한다 — 이유만 주면 같은 호출을 반복한다.


def submit_document_escape(language: str, reason: str) -> str:
    """워크스페이스를 벗어나는 경로를 제출했을 때."""
    if _lang(language) == "en":
        return (f"Refused — {reason}. Only workspace-relative paths can be "
                "submitted.")
    return f"거부됨 — {reason}. 워크스페이스 상대 경로만 제출할 수 있다."


def submit_document_missing(language: str, path: str) -> str:
    """선언한 파일이 디스크에 없을 때."""
    if _lang(language) == "en":
        return (f"Refused — the file '{path}' does not exist. Save the document "
                "with Write first, then call submit_document again.")
    return (f"거부됨 — '{path}' 파일이 없다. Write로 문서를 먼저 저장한 뒤 "
            "submit_document를 다시 호출할 것.")


def submit_document_empty(language: str, path: str) -> str:
    """선언한 파일이 비어 있을 때."""
    if _lang(language) == "en":
        return (f"Refused — '{path}' is empty. Fill in the content with Write, "
                "then call submit_document again.")
    return (f"거부됨 — '{path}'가 비어 있다. Write로 내용을 채운 뒤 "
            "submit_document를 다시 호출할 것.")


# ---- 드라이버가 만드는 텍스트 (agent/claude_driver.py) ----


def question_payload_rejected(language: str, reason: str) -> str:
    """AskUserQuestion 페이로드가 폼으로 성립하지 않을 때 모델에게 돌려주는
    거부 메시지(PermissionResultDeny). 모델이 읽고 다시 호출해야 한다."""
    if _lang(language) == "en":
        return (f"Cannot build the question form: {reason}\n"
                "Call AskUserQuestion again with at least one option per "
                "question.")
    return (f"질문을 만들 수 없다: {reason}\n"
            "각 질문에 옵션을 최소 1개 넣어 AskUserQuestion을 다시 호출해라.")


def answer_first(language: str) -> str:
    """진행 중인 질문이 있는데 새 턴이 들어왔을 때 채팅에 흘리는 안내.

    화면 문구가 아니라 **대화 텍스트**다 — 채팅 말풍선에 AI 발화로 남고
    트랜스크립트에도 들어가므로 UI 언어가 아니라 프로젝트 언어를 따른다
    (lib/startMessage.ts와 approvalMarker.ts가 같은 판단을 기록해 뒀다).
    """
    if _lang(language) == "en":
        return ("Please answer the question that is still open — use the "
                "question form in the right-hand panel.")
    return ("진행 중인 질문에 먼저 답변해 주세요 — 우측 패널의 질문 폼을 "
            "이용하세요.")


def write_outside_docs(language: str, path: str) -> str:
    """`aiplc-docs/` 밖 파일 쓰기를 PreToolUse 훅이 거부할 때 모델이 읽는 이유.

    무엇이 걸렸는지 **경로로 지목한다**: 지목이 없으면 모델이 같은 쓰기를 경로만
    바꿔 재시도하며 루프에 빠진다. 그리고 대안을 함께 준다 — 거부만 하면 모델은
    "막혔다"만 알고 스펙을 어디에 쓸지는 모른다(agent/discovery_guard.py 헤더의
    결함 기록 참조).

    **경로를 못박지 않는다.** 예전에는 `prototypes/{slug}/PROTOTYPE-{slug}.md`를
    쓰라고 안내했는데, 그것은 Path B(use-case 우선순위, 3개)의 레이아웃이다.
    Path A.1(Envision 파생, 단일)은 `prototype/prototype-spec.md`가 맞고 슬러그가
    없다(proto/layout.py 헤더). 한쪽을 못박으면 다른 경로의 에이전트에게 틀린
    지시가 되므로, 자기 스테이지 규칙이 정한 자리로 돌려보낸다.
    """
    if _lang(language) == "en":
        return (f"Refused — Discovery may only write under 'aiplc-docs/', and "
                f"'{path}' is outside it. Building and running prototypes is "
                "the Prototypes tab's job, not this chat's. Write the spec to "
                "the path your stage's rules specify under "
                "'aiplc-docs/discovery/', then tell the user to build it from "
                "the Prototypes tab.")
    return (f"거부됨 — Discovery는 'aiplc-docs/' 아래에만 쓸 수 있고 "
            f"'{path}'는 그 밖이다. 프로토타입을 만들고 실행하는 것은 "
            "Prototypes 탭의 일이며 이 대화의 일이 아니다. 스펙은 "
            "'aiplc-docs/discovery/' 아래, 지금 스테이지의 룰이 정한 경로에 "
            "쓰고, 사용자에게 Prototypes 탭에서 빌드하라고 안내할 것.")


def build_command_refused(language: str, fragment: str) -> str:
    """빌드·서버 기동·워크스페이스 밖 파일 생성 명령을 거부할 때의 이유."""
    if _lang(language) == "en":
        return (f"Refused — '{fragment}' builds or serves a prototype, which "
                "Discovery does not do. Only the Prototypes tab can allocate a "
                "port and register with the preview proxy, so anything you "
                "start here appears on no screen. Write the spec and stop "
                "there.")
    return (f"거부됨 — '{fragment}'는 프로토타입을 빌드하거나 서비스하는 "
            "명령이고, Discovery는 그 일을 하지 않는다. 포트를 받아 프리뷰 "
            "프록시에 등록할 수 있는 것은 Prototypes 탭뿐이므로 여기서 띄운 "
            "것은 어느 화면에도 나타나지 않는다. 스펙 작성까지만 할 것.")


def turn_failed(language: str) -> str:
    """CLI가 실패한 턴을 보고했을 때(`ResultMessage.is_error`) 채팅에 남기는 문구.

    다시 시도하라고 말하는 이유는 흔한 원인이 일시적이기 때문이다 — 워크숍
    부하에서의 Bedrock 429/529는 다음 시도에 성공한다. 진단 정보
    (`api_error_status`)는 로그로 간다: HTTP 상태는 워크숍 참가자가 할 수 있는
    일이 아니다.
    """
    if _lang(language) == "en":
        return ("This turn failed — please try again in a moment. If it keeps "
                "happening, let an administrator know.")
    return ("이번 턴이 실패했습니다 — 잠시 후 다시 시도해 주세요. "
            "반복되면 관리자에게 알려주세요.")


def answers_resumed(language: str, lines: str, record: str) -> str:
    """백엔드 재시작 후 답변을 일반 텍스트 턴으로 전달하는 프롬프트.

    `record`는 이 답변이 어느 질문 라운드에 속하는지의 기계 판독 기록이다.
    로그가 아니라 프롬프트에 있는 이유: 재시작 후 S3 pending 레코드는 나가는
    길에 삭제되므로, 트랜스크립트가 그 대응의 유일한 영속 흔적이 된다.
    """
    if _lang(language) == "en":
        return ("[Question answered] The user has answered the question you "
                "asked. Take these answers into account and carry on.\n"
                f"{lines}\n(answer record)\n{record}")
    return ("[질문 답변] 앞서 드린 질문에 사용자가 답했습니다. "
            "이 답변을 반영해 이어서 진행해 주세요.\n"
            f"{lines}\n(답변 기록)\n{record}")
