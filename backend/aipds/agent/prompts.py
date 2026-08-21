# backend/aipds/agent/prompts.py — Discovery 에이전트가 **읽는** 텍스트의
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
            "AskUserQuestion is not available in AI-PDS. Ask by **writing the "
            "question file** that `common/question-format-guide.md` already "
            "specifies — the numbered questions, their lettered options, and an "
            "`[Answer]:` line under each — anywhere under `aiplc-docs/`. "
            "AI-PDS reads that file the moment you finish writing it and "
            "shows the questions to the user exactly as you wrote them, so the "
            "4-question / 4-option limits of this tool do not apply and nothing "
            "has to be shortened. Your turn ends there; the answers arrive in the "
            "file's `[Answer]:` tags and you continue on the next turn.")
    return (
        "AI-PDS에서는 AskUserQuestion을 쓸 수 없습니다. 질문은 "
        "`common/question-format-guide.md`가 이미 규정한 **질문 파일을 쓰는 것**으로 "
        "합니다 — 번호가 붙은 문항, letter가 붙은 보기, 각 문항 아래 `[Answer]:` 줄. "
        "위치는 `aiplc-docs/` 아래 어디든 됩니다. 파일을 다 쓰는 순간 AI-PDS가 "
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
            f"AI-PDS could not read the questions in `{path}`, so nothing was "
            f"shown to the user. The file has `[Answer]:` tags but no question the "
            f"parser recognizes. Each question needs a heading of the form "
            f"`## Question <number>` — ASCII, exactly as "
            f"`common/question-format-guide.md` writes it — followed by the question "
            f"sentence, its lettered options, and an `[Answer]:` line. Rewrite the "
            f"file that way; the questions appear as soon as you do.")
    return (
        f"`{path}`의 질문을 AI-PDS가 읽지 못해 사용자에게 아무것도 표시되지 "
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
    물으라고 지시하는데, 이 경로에서는 AI-PDS가 파일을 그대로 화면에 띄운다.
    그 사실을 말해 주지 않으면 다음 턴에 모델이 AskUserQuestion으로 같은 질문을
    다시 만들고 — 그것이 2026-08-17에 문항 79%를 훼손한 바로 그 재생성이다.
    """
    if _lang(language) == "en":
        return (f"Stopping here: AI-PDS is showing the questions in "
                f"`{path}` to the user, read from the file exactly as you wrote "
                f"them. Do NOT ask them again with AskUserQuestion and do not "
                f"restate them in chat. The user's answers will be written into "
                f"the file's `[Answer]:` tags; read the file again on the next "
                f"turn and continue from there.")
    return (f"여기서 멈춥니다: `{path}`의 질문을 AI-PDS가 **파일에서 그대로 "
            f"읽어** 사용자 화면에 띄웁니다. AskUserQuestion으로 다시 묻지 말고 "
            f"채팅에 옮겨 적지도 마세요. 사용자의 답변은 그 파일의 `[Answer]:` "
            f"태그에 기록되니, 다음 턴에 파일을 다시 읽고 이어가세요.")


def _answer_lines(answers: dict, label: str) -> str:
    """답변을 `- {label}{n}: {값}` 목록으로. 문항 번호 오름차순.

    번호로 정렬하는 이유: dict 순서는 프론트가 보낸 JSON 키 순서이고, 사용자가
    문항을 건너뛰며 답하면 그 순서가 화면과 어긋난다.
    """
    def order(item):
        try:
            return (0, int(item[0]))
        except (TypeError, ValueError):
            return (1, str(item[0]))
    return "\n".join(f"- {label}{n}: {value}"
                     for n, value in sorted(answers.items(), key=order))


def file_answers_recorded(language: str, path: str, answers: dict) -> str:
    """질문 파일에 답변이 기록된 뒤 에이전트를 다시 부르는 턴의 텍스트.

    `file_questions_stop`이 끝낸 턴을 이어받는다. 파킹된 future로 같은 턴을
    재개하는 것이 아니라 **새 턴**이므로, 어디로 돌아가야 하는지를 이 문장이
    전부 말해 줘야 한다 — 그래서 파일을 지목한다(질문 파일이 여러 개인 것이
    정상이다; 실측한 한 프로젝트에 9개였다).

    사용자 말풍선으로 남는 대화 텍스트다 — UI 언어가 아니라 프로젝트 언어를
    따른다(`answer_first`가 같은 판단을 기록해 뒀다).

    **답변을 본문에 담는다(2026-08-21).** 예전에는 파일만 지목했고, 그 결과
    워크스페이스를 새로고침해 대화가 복원되면 **모든 라운드의 사용자 말풍선이 이
    한 문구로 고정됐다.** 라이브 화면은 실제 답변을 그리므로(프론트의
    `answerSummary`) 같은 라운드가 새로고침 전후로 다르게 보였다 —
    `agent/answer_store.py` 헤더가 기록한 그 결함이 파일 질문 경로에서 되살아난
    것이다.

    그쪽은 `tool_use_id`로 레코드를 조인해 고쳤다. 이 경로에는 그 도구 호출이 아예
    없다(에이전트가 부르는 것은 `Write`다). 그래서 **트랜스크립트 자체가 진실을
    갖게 한다** — 조인 키도, 새 저장소도, 순서 의존도 생기지 않는다.

    **불투명 마커로 조인하지 않는 이유**는 `frontend/lib/approvalMarker.ts`가 이
    부류에 대해 이미 적어 뒀다: "이 텍스트는 기계 신호가 아니다. 그 턴은
    트랜스크립트와 채팅 히스토리에 **사용자 말풍선으로 남는다.** 에이전트가
    이해해야 하고 **사람이 읽어야 한다.**" 질문에 답한 사람이 할 말은 자기
    답변이므로, 답변을 담는 것이 그 원칙과 같은 방향이다.

    **파일은 여전히 권위다.** 값은 두 곳(이 문장과 `[Answer]:` 태그)에 있지만 같은
    요청에서 같은 dict로 만들어지므로 드리프트가 불가능하고, 문장은 파일을 정본으로
    지목한다 — 에이전트가 되읽는 대상은 바뀌지 않는다.
    """
    listed = _answer_lines(answers, "Q" if _lang(language) == "en" else "")
    if _lang(language) == "en":
        return (f"I answered the questions:\n\n{listed}\n\n"
                f"They are also recorded in the `[Answer]:` tags of `{path}` — "
                f"read the file and continue from where you stopped. Do not ask "
                f"them again.")
    return (f"질문에 답했습니다:\n\n{listed}\n\n"
            f"같은 답변이 `{path}`의 `[Answer]:` 태그에도 기록됐습니다 — 파일을 "
            f"읽고 멈춘 지점부터 이어가 주세요. 다시 묻지 마세요.")


def state_file_missing(language: str) -> str:
    """`aiplc-state.md`에 Current Stage가 없을 때 재개 턴에 덧붙이는 지목.

    `file_answers_recorded`에 이어 붙는 **완성된 문단**이다. 조각 치환이 아니라
    문단 연결이므로 이 파일 헤더의 "두 벌을 완성문으로" 규율을 지킨다.

    **왜 백엔드가 대신 쓰지 않는가.** 스테이지 **이름**을 아는 것은 에이전트뿐이다.
    경로에서 추측하려면(`.../envision/xxx-questions.md` → "Envision") 룰셋의
    스테이지 이름을 복제한 매핑이 새로 생기는데, 그것은 룰셋 교체 때 조용히
    어긋나는 두 번째 진실 공급원이다.

    **2026-08-18에 도구에서 파일로 바뀌었다.** 옛 문구는 `report_stage`를 부르라고
    했다. 그 도구가 PostToolUse 훅으로 대체되면서(agent/reconcile.py) 이 지목의
    대상도 도구 호출에서 **파일 쓰기**로 옮겨 왔다 — 그리고 그것이 상류 룰이
    원래 요구하는 행동이다(`common/workflow-changes.md`,
    `discovery/prototype-validation.md` Step 10).

    지목이 여전히 필요한 이유: 훅과 턴 경계 재조정은 파일이 **있을 때** 그것을
    화면으로 옮긴다. 파일 자체가 없으면 옮길 것이 없고, 그것을 만들 수 있는 것은
    에이전트뿐이다.
    """
    if _lang(language) == "en":
        return ("Also: `aiplc-docs/aiplc-state.md` still has no current stage, so "
                "the stage badges are empty. Write that file the way "
                "`common/workflow-changes.md` and your stage's rules specify — a "
                "`- **Current Stage**: <name>` line and a `## Stage Progress` "
                "checklist — and keep it updated as you go. AI-PDS reads the "
                "file and updates the badges itself; there is no tool to call.")
    return ("그리고 `aiplc-docs/aiplc-state.md`에 현재 스테이지가 아직 없어서 "
            "스테이지 배지가 비어 있습니다. `common/workflow-changes.md`와 지금 "
            "스테이지의 룰이 정한 형태로 그 파일을 써 주세요 — "
            "`- **Current Stage**: <이름>` 줄과 `## Stage Progress` 체크리스트 — "
            "그리고 진행하면서 계속 갱신해 주세요. AI-PDS가 그 파일을 읽어 "
            "배지를 스스로 갱신합니다. 호출할 도구는 없습니다.")


def prototype_handoff_stop(language: str, slug: str) -> str:
    """`build-instructions.md`를 쓰는 순간 턴을 멈출 때 모델이 읽는 이유.

    PostToolUse 훅의 `stopReason`으로 간다(`file_questions_stop`과 같은 경로).

    **다음 행동을 지정하는 것이 요점이다.** 옛 `handoff_prototype` 도구의 성공
    문구가 이미 이 판단을 기록해 뒀다: 지정하지 않으면 에이전트가 상류 Step 4
    (Iterate)로 계속 가거나 자격증명을 묻는다 — 둘 다 실측된 실패다(keumkang-v5:
    자격증명 점검 → API 키 요구 → 선행 조건 나열, 탭 안내 0회).

    도구가 훅이 되면서 달라진 것은 **누가 호출을 보장하는가**뿐이다. 문구가 담아야
    하는 내용은 그대로다: 여기서 끝난다, 빌드는 탭에서 한다, 자격증명을 묻지 않는다,
    Step 4-6은 버린 것이 아니라 미룬 것이다.
    """
    if _lang(language) == "en":
        return (f"Stopping here: '{slug}' is now a card in the Prototypes tab, and "
                f"AI-PDS put it there when you wrote the build instructions — "
                f"there is no tool to call. **End your turn** and tell the user to "
                f"build it in that tab. Do not ask for credentials, an API key, a "
                f"provider or a model; the project already has them. Iteration and "
                f"validation (Steps 4-6) are deferred, not abandoned — resume them "
                f"when the user comes back with a built prototype or survey "
                f"results.")
    return (f"여기서 멈춥니다: '{slug}'가 Prototypes 탭의 카드로 준비됐습니다 — "
            f"빌드 지시를 쓰는 순간 AI-PDS가 등록했으므로 호출할 도구는 "
            f"없습니다. **턴을 끝내고** 사용자에게 그 탭에서 빌드하라고 안내해 "
            f"주세요. 자격증명·API 키·제공자·모델을 묻지 마세요 — 프로젝트가 이미 "
            f"갖고 있습니다. 개선과 검증(Step 4-6)은 버린 것이 아니라 미룬 "
            f"것입니다. 사용자가 빌드와 설문을 마치고 돌아오면 재개하세요.")


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
