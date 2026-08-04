# backend/pathfinder/proto/prompts.py — 빌드 에이전트의 개시 프롬프트, 언어별.
#
# **조립하지 않고 언어별로 완성된 문장 두 벌을 유지한다.** 이 프롬프트는 빌드
# 에이전트의 유일한 브레이크다(proto/session.py의 first_prompt docstring):
# 빌더는 bypassPermissions로 돌아 Write/Edit이 자동 승인되므로, "계획만 세우고
# 빌드하지 마"를 이 텍스트 밖에서 강제할 방법이 없다. 문장을 쪼개 치환하면 그
# 지시의 강도가 어느 언어에서 약해졌는지 알 수 없게 된다 — 그것이 두 벌을
# 유지하는 비용을 감수하는 이유다.
#
# 한국어 문장은 종전 session.py·tools.py의 것을 그대로 옮긴 것이다(워크숍에서
# 검증된 문구를 다시 쓰지 않는다). 영어는 같은 지시를 같은 순서로 옮긴 것이고,
# test_proto_prompts가 두 벌에 같은 항목이 있는지 검사한다.
from __future__ import annotations

_LANGUAGES = ("ko", "en")
_DEFAULT = "ko"


def _lang(language: str) -> str:
    return language if language in _LANGUAGES else _DEFAULT


def plan_prompt(language: str, *, spec_key: str, proxy_path: str) -> str:
    """처음부터 시작하는 세션의 개시 턴. 계획만 세우고 빌드하지 않는다."""
    if _lang(language) == "en":
        return (
            f"Read `{spec_key}` and draw up a plan for building this prototype.\n"
            "**In this turn, plan only — do not start building.**\n\n"
            "How to proceed:\n"
            f"1. First read `{spec_key}` and get the requirements exactly right.\n"
            "2. Then present your implementation plan. Include the tech stack, the "
            "list of screens and features you will build, the file structure, and "
            "the order of work; also state what was ambiguous in the spec and what "
            "you assumed on your own.\n"
            "3. After presenting the plan, **you MUST use AskUserQuestion to ask "
            "whether to execute it as written or change something, and wait for my "
            "answer.** Do not move on without approval.\n"
            "4. Do not create or modify any file during the planning stage "
            "(no Write/Edit). Touch nothing except reading the spec, and show the "
            "plan in the message body only.\n"
            "5. Start building only after I approve. While building, if anything is "
            "uncertain or needs a decision, do not decide it on your own — ask with "
            "AskUserQuestion first.\n\n"
            "Rules for the build stage (apply after approval):\n"
            "- Put the finished work under `prototype/` in the working directory, "
            "and write a README explaining how to build and run it.\n"
            f"- This prototype is served under a path proxy (e.g. `{proxy_path}`). "
            "Use basePath and relative paths so it works correctly no matter which "
            "sub-path it is placed under (never hardcode absolute paths).\n"
            "- If the code needs to call an LLM, use Amazon Bedrock through the "
            "default credential chain (instance/execution role). Do not hardcode an "
            "API key; read the region and model ID from environment variables.\n"
            "- **Read the model ID from `process.env.BEDROCK_MODEL_ID`** (or the "
            "equivalent for your language). Hosting injects the project's configured "
            "model under that name — a different name, or a specific model ID "
            "baked in as the default, means the model the user chose is ignored. If "
            "you need a fallback when the variable is absent, do not quietly use a "
            "hardcoded model; surface that the setting is missing.\n"
            "- **Write the prototype's own on-screen text in English** — labels, "
            "buttons, headings, placeholder copy, and any sample data a viewer "
            "reads. The prototype is a single-language demo; do not build an i18n "
            "layer into it.\n"
            "- When the prototype is finished, **declare completion with the "
            "`build_complete` tool.** Summarize what you built in `summary`, and put "
            "any remaining work or known limitations in `remaining`. The build "
            "session ends after this declaration, so if work is left, do not declare "
            "it — keep going.\n"
        )
    return (
        f"`{spec_key}` 파일을 읽고, 프로토타입 구현 계획을 세워줘.\n"
        "**이번 턴에서는 계획만 세우고 빌드는 시작하지 마.**\n\n"
        "진행 방식:\n"
        f"1. 먼저 `{spec_key}`를 읽고 요구사항을 정확히 파악해줘.\n"
        "2. 그다음 구현 계획을 제시해줘. 기술 스택, 만들 화면/기능 목록, "
        "파일 구조, 작업 순서를 포함하고, 스펙에서 애매했던 부분과 네가 임의로 "
        "가정한 내용도 함께 밝혀줘.\n"
        "3. 계획을 제시한 뒤 **반드시 AskUserQuestion으로 이 계획대로 실행할지, "
        "수정할 부분이 있는지 물어보고 내 답을 기다려줘.** 승인 없이 다음 단계로 "
        "넘어가면 안 돼.\n"
        "4. 계획 단계에서는 파일을 만들거나 수정하지 마(Write/Edit 금지). "
        "스펙을 읽는 것 외에는 아무것도 건드리지 말고, 계획은 메시지 본문으로만 "
        "보여줘.\n"
        "5. 내가 승인한 뒤에 빌드를 시작해줘. 빌드 중에도 불확실하거나 결정이 "
        "필요한 사항이 있으면 마음대로 넘기지 말고 AskUserQuestion으로 먼저 "
        "물어봐줘.\n\n"
        "빌드 단계에서 지킬 것(승인 후 적용):\n"
        "- 완성물은 반드시 작업 디렉토리 아래 `prototype/`에 두고, 빌드 방법과 "
        "실행 방법을 설명하는 README를 함께 작성해줘.\n"
        f"- 이 프로토타입은 경로 프록시(예: `{proxy_path}`) 하위 경로에서 서빙돼. "
        "basePath와 상대 경로를 사용해서, 어떤 하위 경로에 배치되어도 정상 동작하도록 "
        "구현해줘(절대 경로 하드코딩 금지).\n"
        "- 코드에서 LLM 호출이 필요하면 Amazon Bedrock을 기본 자격증명 체인(인스턴스/"
        "실행 롤)으로 사용해줘. API 키를 코드에 하드코딩하지 말고, 리전과 모델 ID는 "
        "환경변수로 받도록 구현해줘.\n"
        "- **모델 ID는 반드시 `process.env.BEDROCK_MODEL_ID`(또는 언어에 맞는 "
        "동등 표현)로 읽어줘.** 호스팅이 이 이름으로 프로젝트에 설정된 모델을 "
        "주입한다 — 다른 이름을 쓰거나 특정 모델 ID를 기본값으로 박아 두면 "
        "사용자가 고른 모델이 무시된다. 환경변수가 없을 때의 폴백이 필요하면 "
        "하드코딩한 모델로 조용히 넘어가지 말고 설정이 없다는 것을 드러내줘.\n"
        "- **프로토타입 화면의 문구는 한국어로 써줘** — 라벨, 버튼, 헤딩, "
        "플레이스홀더, 그리고 보는 사람이 읽는 샘플 데이터까지. 프로토타입은 "
        "단일 언어 데모이니 i18n 계층을 만들지는 마.\n"
        "- 프로토타입이 완성되면 **`build_complete` 도구로 완료를 선언해줘.** "
        "무엇을 만들었는지 요약(summary)과, 남은 작업이나 알려진 한계가 있으면 "
        "remaining에 적어줘. 이 선언 뒤 빌드 세션이 종료되니, 아직 작업이 "
        "남았으면 선언하지 말고 계속 진행해줘.\n"
    )


def resume_prompt(language: str) -> str:
    """죽은 세션을 이어받는 개시 턴.

    의도적으로 짧다. 에이전트는 이전 트랜스크립트와 만든 것을 이미 갖고 있어서,
    스펙이나 빌드 규칙을 다시 말하면 그가 이미 보는 것과 경쟁만 한다. 이 턴이
    할 일은 그가 혼자 방향을 정하지 않게 막는 것뿐이다.
    """
    if _lang(language) == "en":
        return (
            "Continuing the previous build session.\n"
            "**Do not build or modify anything yet.**\n\n"
            "1. Briefly summarize what has been done so far and what is left.\n"
            "2. Then **use AskUserQuestion to ask what to work on this time, and "
            "wait for my answer.** Offer options so I can choose between continuing "
            "the remaining work and doing something else first.\n"
            "3. Start working only after I choose.\n"
        )
    return (
        "이전 빌드 세션을 이어서 진행한다.\n"
        "**아직 아무것도 빌드하거나 수정하지 마.**\n\n"
        "1. 지금까지 진행한 내용과 남은 작업을 짧게 정리해줘.\n"
        "2. 그다음 **AskUserQuestion으로 이번에 무엇을 진행할지 물어보고 내 답을 "
        "기다려줘.** 남은 작업을 이어서 할지, 다른 것을 먼저 할지 내가 고를 수 "
        "있게 선택지를 제시해줘.\n"
        "3. 내가 고른 뒤에 작업을 시작해줘.\n"
    )


def missing_output_prompt(language: str, *, spec_key: str) -> str:
    """산출물이 사라진 뒤의 개시 턴 — 찾지 말고 다시 만들라고 말한다.

    이 지시가 없으면 에이전트는 트랜스크립트를 믿고 없는 코드를 찾아 나선다.
    실측: 리셋된 프로토타입에서 작업 디렉토리 → 다른 프로토타입 디렉토리 →
    `/opt/pathfinder/frontend` → 파일시스템 전체로 탐색을 넓히며 19초 이상을
    태웠고, 성공할 수 없는 탐색이었다.
    """
    if _lang(language) == "en":
        return (
            "The record of the previous build session is still here, but "
            "**there is no output under `prototype/`** in the working directory. "
            "It was reset, or the build environment was replaced.\n\n"
            "**Do not look for the old code.** It is nowhere in this environment. "
            f"Read `{spec_key}` again and **just build it from scratch.** Reuse the "
            "direction and the decisions from the earlier conversation.\n\n"
            "**Do not start building yet.**\n"
            "1. Read the spec and give me a short implementation plan that reflects "
            "what we agreed on earlier.\n"
            "2. Then **use AskUserQuestion to ask whether to rebuild it this way, "
            "and wait for my answer.**\n"
            "3. Start building only after I approve. Put the finished work under "
            "`prototype/` in the working directory, and declare completion with "
            "`build_complete` when you are done.\n"
        )
    return (
        "이전 빌드 세션의 기록은 남아 있지만, 작업 디렉토리의 "
        "`prototype/`에 **산출물이 없다.** 초기화됐거나 빌드 환경이 "
        "교체된 것이다.\n\n"
        "**이전 코드를 찾지 마.** 이 환경 어디에도 남아 있지 않다. "
        f"`{spec_key}`를 다시 읽고 **처음부터 다시 만들면 된다.** "
        "이전 대화에서 정한 방향과 결정사항은 그대로 활용해줘.\n\n"
        "**아직 빌드는 시작하지 마.**\n"
        "1. 스펙을 읽고, 이전 대화에서 합의된 내용을 반영한 구현 계획을 "
        "짧게 제시해줘.\n"
        "2. 그다음 **AskUserQuestion으로 이 계획대로 다시 만들지 물어보고 내 "
        "답을 기다려줘.**\n"
        "3. 내가 승인한 뒤에 빌드를 시작해줘. 완성물은 작업 디렉토리 아래 "
        "`prototype/`에 두고, 끝나면 `build_complete`로 완료를 선언해줘.\n"
    )


def handoff_prompt(language: str, *, spec_key: str, summary: str,
                   remaining: str) -> str:
    """완료된 빌드를 개선하는 새 세션의 개시 턴.

    파일 트리를 넘기지 않는 것이 의도적이다 — 에이전트가 자기 파일 도구로 cwd를
    읽는 편이 스냅샷보다 정확하다. 여기서 할 일은 이전 빌드가 무엇을 남겼는지
    알려주고 마음대로 손대지 않게 막는 것뿐이다.
    """
    if _lang(language) == "en":
        return (
            "This prototype has already been built once. This session is for "
            "improvements.\n\n"
            f"Summary of the previous build:\n{summary}\n\n"
            f"Recorded as remaining work:\n{remaining}\n\n"
            "**Do not modify anything yet.**\n"
            "1. First look at `prototype/` in the working directory to see where "
            f"things stand. Re-read `{spec_key}` if you need to.\n"
            "2. Then **use AskUserQuestion to ask what to improve this time, and "
            "wait for my answer.** Offer options so I can choose between the "
            "remaining work recorded above and something else.\n"
            "3. Start working only after I choose. When the improvements are done, "
            "declare completion with `build_complete` again.\n"
        )
    return (
        "이 프로토타입은 이미 한 번 빌드가 완료됐다. 이번 세션은 개선 "
        "작업이다.\n\n"
        f"이전 빌드 요약:\n{summary}\n\n"
        f"남은 작업으로 기록된 것:\n{remaining}\n\n"
        "**아직 아무것도 수정하지 마.**\n"
        f"1. 먼저 작업 디렉토리의 `prototype/`을 살펴보고 현재 상태를 파악해줘. "
        f"필요하면 `{spec_key}`도 다시 읽어줘.\n"
        "2. 그다음 **AskUserQuestion으로 이번에 무엇을 개선할지 물어보고 내 "
        "답을 기다려줘.** 위에 기록된 남은 작업을 할지, 다른 것을 할지 내가 "
        "고를 수 있게 선택지를 제시해줘.\n"
        "3. 내가 고른 뒤에 작업을 시작해줘. 개선이 끝나면 다시 "
        "`build_complete`로 완료를 선언해줘.\n"
    )


def build_complete_description(language: str) -> str:
    """`build_complete` 도구 설명. 도구 설명은 모델이 읽는 프롬프트다."""
    if _lang(language) == "en":
        return ("Declare that the prototype build is complete. Call this only "
                "**after you have produced real output under `prototype/`** — an "
                "empty directory means the declaration is rejected. The build "
                "session ends after this declaration, so do not call it while work "
                "remains.")
    return ("프로토타입 빌드가 완료되었음을 선언한다. **prototype/ 아래에 실제 "
            "산출물을 만든 뒤** 호출해야 한다 — 비어 있으면 선언이 거부된다. "
            "이 선언 뒤 빌드 세션이 종료되므로, 아직 작업이 남았으면 호출하지 마라.")


def build_complete_rejection(language: str) -> str:
    """산출물 없이 완료를 선언했을 때 모델에게 돌려주는 거부 메시지."""
    if _lang(language) == "en":
        return ("Rejected — there is no output under `prototype/` in the working "
                "directory. Write the finished work to `prototype/` and declare "
                "completion again.")
    return ("거부됨 — 작업 디렉토리의 `prototype/` 아래에 산출물이 없다. "
            "완성물을 `prototype/`에 쓴 뒤 다시 선언해라.")


def build_complete_recorded(language: str) -> str:
    """완료 선언을 받아들였을 때 모델에게 돌려주는 확인."""
    if _lang(language) == "en":
        return "Build completion recorded. Ending the session."
    return "빌드 완료가 기록되었다. 세션을 종료한다."


def session_already_complete(language: str) -> str:
    """완료 선언 뒤 메시지를 받았을 때 사용자에게 보이는 안내."""
    if _lang(language) == "en":
        return ("This build session is already complete and cannot take more "
                "messages. To keep improving, start a new session with "
                "\"Continue improving\".")
    return ("이 빌드 세션은 이미 완료되어 더 이상 메시지를 받을 수 "
            "없습니다. 개선 작업이 필요하면 '개선 이어서 하기'로 "
            "새 세션을 시작해 주세요.")


def missing_remaining_note(language: str) -> str:
    """handoff에서 남은 작업 기록이 없을 때 쓰는 자리표시."""
    return ("(nothing recorded)" if _lang(language) == "en"
            else "(따로 기록된 것 없음)")
