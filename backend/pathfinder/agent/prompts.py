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


def handoff_prototype_description(language: str) -> str:
    """`handoff_prototype` 도구 설명.

    **이 문장이 이 도구의 절반이다.** 도구 목록은 매 턴 컨텍스트에 들어가므로,
    정적 규칙 문서보다 확실하게 "빌드는 내 일이 아니다"를 전달한다. 2026-08-17에
    금지만 있고 대체 행동이 없어서 에이전트가 자격증명을 묻고 선행 조건을
    나열했다 — 그 자리를 이 도구가 채운다.
    """
    if _lang(language) == "en":
        return ("Hand the finished prototype spec over to the Prototypes tab, "
                "which is where builds and hosting happen. Call this instead of "
                "building: you cannot build here, and the project's model and "
                "credentials are already provisioned — never ask the user for an "
                "API key, a provider or a model. `slug` names a spec you have "
                "already written; for a single-prototype project it is "
                "'prototype'. Then end your turn.")
    return ("완성된 프로토타입 명세를 Prototypes 탭으로 넘긴다 — 빌드와 호스팅은 "
            "그곳에서 일어난다. 빌드를 시도하는 대신 이 도구를 호출한다: 여기서는 "
            "빌드할 수 없고, 프로젝트의 모델과 자격증명은 이미 준비되어 있다 — "
            "API 키·제공자·모델을 사용자에게 묻지 않는다. `slug`는 **이미 써 둔** "
            "명세의 id다 — 단일 프로토타입 프로젝트에서는 'prototype'이다. "
            "호출한 뒤 턴을 끝낸다.")


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


def handoff_prototype_unknown(language: str, slug: str,
                              available: list[str]) -> str:
    """넘기려는 id의 명세가 없을 때. **있는 id를 나열한다.**

    옛 문구는 `spec_key(slug)`가 계산한 경로를 지목하며 "룰이 정한 자리에 명세를
    먼저 쓰라"고 했다. 그 문구가 2026-08-18 hpt-sarang에서 실제로 만든 결과:
    에이전트가 제품명으로 슬러그를 지어냈고, 존재하지 않는 그 경로에 파일을
    **새로 만들어** 검사를 통과했다. 단일 해법 프로젝트에 카드가 둘 뜨는 화면이
    됐다. 이유만 말하고 다음 행동을 지정하지 않으면 모델이 즉흥한다는 이 파일
    헤더의 원칙이, 여기서는 **틀린 행동을 지정해서** 어긋난 것이다.

    그래서 지목하는 것을 경로에서 **후보 목록**으로 바꾼다. 고를 것이 화면에
    있으면 지어낼 이유가 없다. 후보가 비어 있는 경우(명세를 아직 안 씀)는
    별도로 말한다 — 그때는 정말로 파일을 써야 한다.
    """
    if _lang(language) == "en":
        if not available:
            return (f"Refused — '{slug}' does not exist, and this workspace has "
                    "no prototype spec at all yet. Write the spec where your "
                    "stage's rules put it, then call handoff_prototype again.")
        listed = ", ".join(f"'{s}'" for s in available)
        return (f"Refused — '{slug}' is not a prototype in this workspace. Hand "
                f"off one of these instead: {listed}. Pick from that list; do "
                "not invent an id and do not create a new spec file to make "
                "one exist.")
    if not available:
        return (f"거부됨 — '{slug}'는 없고, 이 워크스페이스에는 프로토타입 명세가 "
                "아직 하나도 없다. 지금 스테이지의 룰이 정한 자리에 명세를 먼저 "
                "쓴 뒤 handoff_prototype을 다시 호출할 것.")
    listed = ", ".join(f"'{s}'" for s in available)
    return (f"거부됨 — '{slug}'는 이 워크스페이스의 프로토타입이 아니다. 다음 중 "
            f"하나를 넘길 것: {listed}. 이 목록에서 고른다 — id를 지어내지 말고, "
            "지어낸 id를 존재하게 만들려고 새 명세 파일을 만들지도 말 것.")


def handoff_prototype_done(language: str, slug: str) -> str:
    """넘기기 성공. **다음 행동을 지정한다** — 이것이 없으면 에이전트가 상류
    Step 4(Iterate)로 계속 가거나 자격증명을 묻는다. 둘 다 실측된 실패다."""
    if _lang(language) == "en":
        return (f"Handed off: '{slug}' is now a card in the Prototypes tab. "
                "**End your turn here** and tell the user to build it there. Do "
                "not ask for credentials, an API key, a provider or a model — the "
                "project already has them. Iteration and validation resume after "
                "they come back with a built prototype and survey results.")
    return (f"넘겼다: '{slug}'가 Prototypes 탭의 카드로 준비됐다. "
            "**여기서 턴을 끝내고** 사용자에게 그 탭에서 빌드하라고 안내할 것. "
            "자격증명·API 키·제공자·모델을 묻지 않는다 — 프로젝트가 이미 갖고 "
            "있다. 개선(Iterate)과 검증은 사용자가 빌드와 설문을 마치고 돌아온 "
            "뒤에 재개한다.")


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


def ask_user_question_denied(language: str) -> str:
    """AskUserQuestion을 거부할 때 모델이 읽는 이유 + 대체 행동.

    **거부이지 삭제가 아니다.** 가로채기를 그냥 없애면 모델이 이 도구를 부른 순간
    질문이 조용히 사라진다 — 화면에도 채팅에도 없다. 거부는 대체 행동을 손에
    쥐여 주므로 그 구멍이 생기지 않는다(`write_outside_docs`가 같은 패턴이고,
    거부만 하면 모델이 경로만 바꿔 재시도하며 루프에 빠진다는 기록이 그 함수에 있다).

    왜 이 도구를 쓰지 않는가(2026-08-17 실측): 파일에 이미 쓴 질문을 이 도구의
    입력으로 다시 만들면서 19문항 중 15개(79%)가 훼손됐다 — 한글 문자 치환 11건,
    축약으로 답변 유실 4건. 파일을 그대로 읽으면 그 실패 종류가 사라진다.
    """
    if _lang(language) == "en":
        return (
            "AskUserQuestion is not available in Pathfinder. Ask by **writing the "
            "question file** that `common/question-format-guide.md` already "
            "specifies — the numbered questions, their lettered options, and an "
            "`[Answer]:` line under each — anywhere under `aiplc-docs/`. "
            "Pathfinder reads that file the moment you finish writing it and "
            "shows the questions to the user exactly as you wrote them, so the "
            "4-question / 4-option limits of this tool do not apply and nothing "
            "has to be shortened. Your turn ends there; the answers arrive in the "
            "file's `[Answer]:` tags and you continue on the next turn.")
    return (
        "Pathfinder에서는 AskUserQuestion을 쓸 수 없습니다. 질문은 "
        "`common/question-format-guide.md`가 이미 규정한 **질문 파일을 쓰는 것**으로 "
        "합니다 — 번호가 붙은 문항, letter가 붙은 보기, 각 문항 아래 `[Answer]:` 줄. "
        "위치는 `aiplc-docs/` 아래 어디든 됩니다. 파일을 다 쓰는 순간 Pathfinder가 "
        "그것을 읽어 **적은 그대로** 사용자에게 보여주므로, 이 도구의 4문항·4보기 "
        "제한이 적용되지 않고 무엇도 줄여 쓸 필요가 없습니다. 턴은 거기서 끝나고, "
        "답변은 그 파일의 `[Answer]:` 태그로 들어오며 다음 턴에 이어갑니다.")


def file_questions_unparsed(language: str, path: str) -> str:
    """질문 파일에 `[Answer]:`는 있는데 문항을 읽을 수 없을 때 모델에게 주는 노트.

    PostToolUse 훅의 `additionalContext`로 간다 — **턴을 멈추지 않는다.** 멈추면
    모델이 이 노트를 읽고 고칠 기회가 없어 사용자가 막힌다. 실측(2026-08-17):
    모델은 이 노트를 읽고 같은 턴 안에서 파일을 고쳐 다시 쓰고, 그 재작성이 훅을
    다시 태워 정상 카드가 뜬다.

    **왜 침묵하면 안 되는가.** 처음에는 조용히 지나가게 만들었고, 그 판단은
    AskUserQuestion이 폴백으로 살아 있을 때만 옳았다. 그 도구가 거부되는 지금
    파싱 실패는 질문의 완전한 소실이다 — sarang-hpt에서 그렇게 됐다: 파일은
    만들어졌고 카드는 뜨지 않았고 채팅에도 아무 말이 없었다.

    무엇을 고쳐야 하는지 **지목한다**. 이유만 주면 모델이 같은 파일을 다시 쓴다
    (`write_outside_docs`가 같은 이유로 대안을 함께 준다).
    """
    if _lang(language) == "en":
        return (
            f"Pathfinder could not read the questions in `{path}`, so nothing was "
            f"shown to the user. The file has `[Answer]:` tags but no question the "
            f"parser recognizes. Each question needs a heading of the form "
            f"`## Question <number>` — ASCII, exactly as "
            f"`common/question-format-guide.md` writes it — followed by the question "
            f"sentence, its lettered options, and an `[Answer]:` line. Rewrite the "
            f"file that way; the questions appear as soon as you do.")
    return (
        f"`{path}`의 질문을 Pathfinder가 읽지 못해 사용자에게 아무것도 표시되지 "
        f"않았습니다. `[Answer]:` 태그는 있는데 파서가 인식하는 문항이 없습니다. "
        f"각 문항에는 `## Question <번호>` 형태의 헤딩이 필요합니다 — "
        f"`common/question-format-guide.md`가 적은 그대로 ASCII로 씁니다(본문과 "
        f"보기는 프로젝트 언어로 씁니다). 그 뒤에 질문 문장, letter가 붙은 보기, "
        f"`[Answer]:` 줄이 옵니다. 그렇게 다시 쓰면 질문이 바로 표시됩니다.")


def file_questions_stop(language: str, path: str) -> str:
    """질문 파일을 쓰는 순간 턴을 멈출 때 모델이 읽는 이유.

    PostToolUse 훅의 `stopReason`으로 간다. 모델 컨텍스트에 들어가므로 프로젝트
    언어를 따른다.

    **다시 묻지 말라고 명시하는 것이 요점이다.** 상류 룰은 질문을 만든 뒤 사용자에게
    물으라고 지시하는데, 이 경로에서는 Pathfinder가 파일을 그대로 화면에 띄운다.
    그 사실을 말해 주지 않으면 다음 턴에 모델이 AskUserQuestion으로 같은 질문을
    다시 만들고 — 그것이 2026-08-17에 문항 79%를 훼손한 바로 그 재생성이다.
    """
    if _lang(language) == "en":
        return (f"Stopping here: Pathfinder is showing the questions in "
                f"`{path}` to the user, read from the file exactly as you wrote "
                f"them. Do NOT ask them again with AskUserQuestion and do not "
                f"restate them in chat. The user's answers will be written into "
                f"the file's `[Answer]:` tags; read the file again on the next "
                f"turn and continue from there.")
    return (f"여기서 멈춥니다: `{path}`의 질문을 Pathfinder가 **파일에서 그대로 "
            f"읽어** 사용자 화면에 띄웁니다. AskUserQuestion으로 다시 묻지 말고 "
            f"채팅에 옮겨 적지도 마세요. 사용자의 답변은 그 파일의 `[Answer]:` "
            f"태그에 기록되니, 다음 턴에 파일을 다시 읽고 이어가세요.")


def file_answers_recorded(language: str, path: str) -> str:
    """질문 파일에 답변이 기록된 뒤 에이전트를 다시 부르는 턴의 텍스트.

    `file_questions_stop`이 끝낸 턴을 이어받는다. 파킹된 future로 같은 턴을
    재개하는 것이 아니라 **새 턴**이므로, 어디로 돌아가야 하는지를 이 문장이
    전부 말해 줘야 한다 — 그래서 파일을 지목한다(질문 파일이 여러 개인 것이
    정상이다; 실측한 한 프로젝트에 9개였다).

    사용자 말풍선으로 남는 대화 텍스트다 — UI 언어가 아니라 프로젝트 언어를
    따른다(`answer_first`가 같은 판단을 기록해 뒀다).
    """
    if _lang(language) == "en":
        return (f"I answered the questions. My answers are now in the "
                f"`[Answer]:` tags of `{path}` — read the file and continue from "
                f"where you stopped. Do not ask them again.")
    return (f"질문에 답했습니다. 답변은 `{path}`의 `[Answer]:` 태그에 들어 "
            f"있으니, 파일을 읽고 멈춘 지점부터 이어가 주세요. 다시 묻지 "
            f"마세요.")


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
