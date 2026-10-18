# backend/aipds/agent/prompts.py — the text the Discovery agent **reads**, in two
# versions, one per language.
#
# Everything here goes into the model context: MCP tool descriptions (injected with the
# tool list on every turn), the refusal/confirmation strings tools return, and the prompts
# the driver builds. It is not screen text — UI strings belong to frontend/lib/i18n.
#
# **Why this file exists (the 2026-08-04 defect).** Spec
# `2026-08-03-bilingual-ko-en` §3 made `place_rules` the "single source of the language
# instruction", and that part worked exactly (0 Hangul characters in the en assembly). Yet the
# workspace chat of projects that chose English kept going in Korean. The remaining paths were
# these — tool descriptions and tool return values are **not instructions**, so they were outside
# that spec's view, but to the model they are Korean prompts it reads on every turn all the same.
#
# So this repo's language criterion is not "is there a single language instruction" but
# **"is every text that goes into the model context in the project language"**.
#
# **Keep two complete versions instead of assembling.** `proto/prompts.py` records the same
# judgment: splitting sentences into fragments and substituting makes it impossible to tell in
# which language an instruction lost strength. Here that applies especially to submit_document's
# ordering instruction, "write the file first, then call" — if that instruction weakens, the tool
# accepts false declarations and the document panel shows an empty screen.
from __future__ import annotations

from pathlib import PurePosixPath

from aipds.proto.layout import CHANGE_HISTORY

#: Supported languages. Must be the same set as workspace_rules._LANGUAGES.
_LANGUAGES = ("ko", "en")
_DEFAULT = "ko"


def _lang(language: str) -> str:
    """Unknown values fall back to the default — the route validates at creation time, so
    they cannot come in on the normal path, and running in Korean beats running without
    a prompt because of a corrupted manifest (same discipline as place_rules)."""
    return language if language in _LANGUAGES else _DEFAULT


# ---- MCP tool descriptions and return strings used to live here ----
#
# There were four: `submit_document_description` and its three refusal strings (path escape, missing file,
# empty file). They went away on 2026-08-21 together with the `submit_document` tool — Discovery has no
# custom tools left, so this spot is rightly empty.
#
# What the refusal strings guarded ("a tool cannot declare a document that does not exist") became an
# impossible failure once the verdict moved from the tool to a hook: the `document` event is derived
# **from the fact that a file was written** (agent/reconcile.document_events), so there is no way to
# announce a missing document. Only the empty-file condition remains, and it lives inside that function.


# ---- Text the driver builds (agent/claude_driver.py) ----


def question_payload_rejected(language: str, reason: str) -> str:
    """Refusal message (PermissionResultDeny) returned to the model when an AskUserQuestion
    payload does not form a valid form. The model must read it and call again."""
    if _lang(language) == "en":
        return (f"Cannot build the question form: {reason}\n"
                "Call AskUserQuestion again with at least one option per "
                "question.")
    return (f"질문을 만들 수 없다: {reason}\n"
            "각 질문에 옵션을 최소 1개 넣어 AskUserQuestion을 다시 호출해라.")


def answer_first(language: str) -> str:
    """Notice posted to the chat when a new turn arrives while a question is still open.

    It is **conversation text**, not screen text — it stays in the chat as an AI bubble and
    also goes into the transcript, so it follows the project language, not the UI language
    (lib/startMessage.ts and approvalMarker.ts record the same judgment).
    """
    if _lang(language) == "en":
        return ("Please answer the question that is still open — use the "
                "question form in the right-hand panel.")
    return ("진행 중인 질문에 먼저 답변해 주세요 — 우측 패널의 질문 폼을 "
            "이용하세요.")


def ask_user_question_denied(language: str) -> str:
    """The reason plus alternative action the model reads when AskUserQuestion is refused.

    **It is a refusal, not a removal.** Simply removing the interception makes the question
    vanish silently the moment the model calls this tool — it is on neither the screen nor the chat.
    A refusal puts an alternative action in its hands, so that hole never opens (`write_outside_docs`
    uses the same pattern, and that function records that refusing alone sends the model into a loop of retrying with only the path changed).

    Why this tool is not used (observed 2026-08-17): re-creating questions already written to a file as this
    tool's input damaged 15 of 19 questions (79%) — 11 Hangul character substitutions and
    4 answers lost to abbreviation. Reading the file as is removes that class of failure.
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
    """Note given to the model when a question file has `[Answer]:` but its questions cannot be read.

    It goes out as the PostToolUse hook's `additionalContext` — **it does not stop the turn.** Stopping
    would leave the model no chance to read this note and fix the file, and the user would be stuck. Observed (2026-08-17):
    the model reads this note, fixes and rewrites the file within the same turn, and that rewrite fires the hook
    again so a proper card appears.

    **Why it must not stay silent.** At first it was made to pass silently, and that judgment was
    right only while AskUserQuestion was alive as a fallback. Now that the tool is refused,
    a parse failure means the question is lost entirely — that is what happened in ws-j: the file
    was created, no card appeared, and nothing was said in the chat.

    It **points out** what to fix. Given only the reason, the model rewrites the same file
    (`write_outside_docs` gives an alternative for the same reason).
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
    """The reason the model reads when the turn is stopped the moment it writes a question file.

    It goes out as the PostToolUse hook's `stopReason`. It goes into the model context, so it follows
    the project language.

    **The point is to say explicitly not to ask again.** The upstream rules tell the model to ask the user
    after creating questions, but on this path AI-PDS puts the file on screen as is.
    Without being told that, the model re-creates the same questions with AskUserQuestion on the next
    turn — exactly the regeneration that damaged 79% of the questions on 2026-08-17.
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


# `_answer_lines` used to live here. It lined answers up as `- 1: A,B`, and that was the limit
# of the morning fix (fe6a482) — it did not resolve option letters into labels, so the record differed
# from the live screen. Rendering is now owned in one place by `aipds/answer_summary.py` (the frontend's
# detection was merged there too). This file only wraps that result in instructions for the model.


def file_answers_recorded(language: str, path: str, summary: str) -> str:
    """Text of the turn that calls the agent again after answers are recorded in a question file.

    It picks up the turn `file_questions_stop` ended. It is a **new turn**, not a resumption of the
    same turn through a parked future, so this sentence must say everything about where to return
    — hence it points at the file (several question files is
    normal; one observed project had 9).

    It is conversation text that stays as a user bubble — it follows the project language, not the UI
    language (`answer_first` records the same judgment).

    **The answers go in the body (2026-08-21).** It used to point only at the file, and as a result,
    when the workspace was refreshed and the conversation restored, **every round's user bubble was
    frozen to this one sentence.** The live screen draws the actual answers (the frontend's
    `answerSummary`), so the same round looked different before and after a refresh —
    the defect recorded in the `agent/answer_store.py` header came back on the file-question
    path.

    That one was fixed by joining records on `tool_use_id`. This path has no such tool call at all
    (what the agent calls is `Write`). So **the transcript itself is made to hold the
    truth** — no join key, no new store, no ordering dependency.

    **Why not join through an opaque marker**: `frontend/lib/approvalMarker.ts` has already written this
    down for this class of text: "This text is not a machine signal. That turn **stays in the
    transcript and chat history as a user bubble.** The agent must
    understand it and **a person must read it.**" What a person who answered questions has to say is their
    answers, so carrying the answers goes in the same direction as that principle.

    **The file is still the authority.** The values live in two places (this sentence and the `[Answer]:` tags), but they are
    built from the same dict in the same request, so drift is impossible, and the sentence points at the file as
    the source-of-truth copy — what the agent rereads does not change.

    **`summary` is already-rendered text (2026-08-21).** While this function lined up the answers itself,
    option letters stayed as they were ("- 1: A,B") and the live screen drew them resolved into labels
    — the record and the screen still differed. Rendering is owned in one place by `aipds/answer_summary.py`,
    and **the frontend uses this string as is too** (routes/answers.py includes it in the
    response). With one representation, they cannot diverge.
    """
    if _lang(language) == "en":
        return (f"I answered the questions:\n\n{summary}\n\n"
                f"They are also recorded in the `[Answer]:` tags of `{path}` — "
                f"read the file and continue from where you stopped. Do not ask "
                f"them again.")
    return (f"질문에 답했습니다:\n\n{summary}\n\n"
            f"같은 답변이 `{path}`의 `[Answer]:` 태그에도 기록됐습니다 — 파일을 "
            f"읽고 멈춘 지점부터 이어가 주세요. 다시 묻지 마세요.")


def state_file_missing(language: str) -> str:
    """Pointer appended to the resume turn when `aiplc-state.md` has no Current Stage.

    It is a **complete paragraph** appended after `file_answers_recorded`. It is paragraph concatenation,
    not fragment substitution, so it keeps this file header's "two complete versions" discipline.

    **Why the backend does not write it instead.** Only the agent knows the stage **name**.
    Guessing it from the path (`.../envision/xxx-questions.md` → "Envision") would create a new mapping
    duplicating the ruleset's stage names, which is a second source of truth that silently drifts
    when the ruleset is swapped.

    **It changed from a tool to a file on 2026-08-18.** The old wording said to call `report_stage`.
    When that tool was replaced by a PostToolUse hook (agent/reconcile.py), the target of this pointer
    also moved from a tool call to **a file write** — and that is the behavior the upstream rules
    originally require (`common/workflow-changes.md`,
    `discovery/prototype-validation.md` Step 10).

    Why the pointer is still needed: the hook and turn-boundary reconciliation move the file to the
    screen **when it exists**. If the file itself does not exist there is nothing to move, and only the
    agent can create it.
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
    """The reason the model reads when the turn is stopped the moment it writes `build-instructions.md`.

    It goes out as the PostToolUse hook's `stopReason` (same path as `file_questions_stop`).

    **The point is to specify the next action.** The success message of the old `handoff_prototype` tool
    already recorded this judgment: without it, the agent carries on to upstream Step 4
    (Iterate) or asks for credentials — both observed failures (ws-a-v5:
    credential check → API key request → list of prerequisites, 0 pointers to the tab).

    The only thing that changed when the tool became a hook is **who guarantees the call**. What the
    message must carry is the same: it ends here, the build happens in the tab, do not ask for credentials,
    Steps 4-6 are deferred, not abandoned.
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
    """The reason the model reads when the PreToolUse hook refuses a file write outside `aiplc-docs/`.

    It **points at the path** that was caught: without that, the model retries the same write with only
    the path changed and falls into a loop. It also gives an alternative — refused alone, the model
    knows only "blocked" and not where to write the spec (see the defect record in the
    agent/discovery_guard.py header).

    **It does not pin the path.** It used to direct writing `prototypes/{slug}/PROTOTYPE-{slug}.md`,
    but that is the layout of Path B (use-case priority, 3 of them).
    For Path A.1 (derived from Envision, single), `prototype/prototype-spec.md` is correct and there is no
    slug (proto/layout.py header). Pinning one would give the agent on the other path the wrong
    instruction, so it sends the model back to the place its own stage's rules define.
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


def web_owned_doc(language: str, path: str) -> str:
    """The reason given when refusing a write to a web-owned `aiplc-docs/` file
    (discovery_guard.WEB_OWNED_NAMES).

    It gives an alternative too (same reason as `write_outside_docs`) — what the model meant to write to that file
    has its own place. What it meant to write to the survey aggregate is the Step 6 synthesis, and its place is
    the rules' `validation-results.md` (proto/layout.SURVEY_AGGREGATE). What it meant to write to the change history
    is the conclusion of the spec reconciliation pass, and its place is `spec-as-built.md`
    (the "Spec reconciliation" section of discovery-config/CLAUDE.md).
    """
    if PurePosixPath(path).name == CHANGE_HISTORY:
        if _lang(language) == "en":
            return (f"Refused — '{path}' is maintained by AI-PDS Web: each build "
                    "session appends what it changed, and the next build session "
                    "reads it as its input. Read it as the evidence for the spec "
                    "reconciliation, and write your conclusions to "
                    "'spec-as-built.md' in the same directory.")
        return (f"거부됨 — '{path}'는 AI-PDS Web이 관리하는 파일이다. 빌드 세션마다 "
                "바꾼 것을 덧붙이고, 다음 빌드 세션이 입력으로 읽는다. 명세 정합성 "
                "패스의 근거로 읽고, 결론은 같은 디렉터리의 'spec-as-built.md'에 쓸 것.")
    if _lang(language) == "en":
        return (f"Refused — '{path}' is maintained by AI-PDS Web and is "
                "rewritten from the survey responses on every 'Synthesize "
                "results', so anything written there is lost. Read it as the "
                "feedback Step 6 imports, and write your synthesis to "
                "'validation-results.md' in the same directory, as "
                "prototype-validation.md specifies.")
    return (f"거부됨 — '{path}'는 AI-PDS Web이 관리하는 파일이고 '결과 취합'을 "
            "누를 때마다 설문 응답으로 다시 만들어지므로, 여기 쓴 내용은 사라진다. "
            "이 파일은 Step 6이 가져오는 피드백으로 읽고, 종합은 "
            "prototype-validation.md가 정한 대로 같은 디렉터리의 "
            "'validation-results.md'에 쓸 것.")


def audit_overwrite(language: str, path: str) -> str:
    """The reason given when trying to overwrite an existing audit.md with Write (discovery_guard.audit_overwrite_denial).

    It gives an alternative: append the same content at the end with Edit. The user's input is not written
    again — the web has already recorded it verbatim (aipds/audit_log).
    """
    if _lang(language) == "en":
        return (f"Refused — '{path}' already exists and AI-PDS Web has recorded the user's "
                "input in it verbatim with the real time. Writing the whole file would "
                "erase that record. Append your entry at the end with Edit instead, and do "
                "not copy the user's input again.")
    return (f"거부됨 — '{path}'는 이미 있고, AI-PDS Web이 사용자 입력을 원문 그대로 실제 "
            "시각과 함께 기록해 두었다. 파일 전체를 쓰면 그 기록이 지워진다. 대신 Edit로 "
            "파일 끝에 항목을 덧붙이고, 사용자 입력은 다시 적지 말 것.")


def build_command_refused(language: str, fragment: str) -> str:
    """The reason given when refusing a command that builds, starts a server, or creates files outside the workspace."""
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


def cli_settings_refused(language: str, fragment: str) -> str:
    """The reason given when Bash touches a CLI settings file (discovery_guard.settings_denial)."""
    if _lang(language) == "en":
        return (f"Refused — '{fragment}' is a Claude Code settings file. AI-PDS Web "
                "provides the settings Discovery needs and resets them on every "
                "launch; there is nothing to change there. Continue with the "
                "documents under aiplc-docs/.")
    return (f"거부됨 — '{fragment}'는 Claude Code 설정 파일이다. Discovery에 필요한 "
            "설정은 AI-PDS Web이 주고 실행할 때마다 다시 맞춘다 — 바꿀 것이 없다. "
            "aiplc-docs/ 아래 문서 작업을 계속할 것.")


def turn_failed(language: str) -> str:
    """Text left in the chat when the CLI reports a failed turn (`ResultMessage.is_error`).

    It says to try again because the common causes are transient — Bedrock 429/529 under workshop
    load succeed on the next attempt. Diagnostics
    (`api_error_status`) go to the log: an HTTP status is not something a workshop participant can
    act on.
    """
    if _lang(language) == "en":
        return ("This turn failed — please try again in a moment. If it keeps "
                "happening, let an administrator know.")
    return ("이번 턴이 실패했습니다 — 잠시 후 다시 시도해 주세요. "
            "반복되면 관리자에게 알려주세요.")


def answers_resumed(language: str, lines: str, record: str) -> str:
    """Prompt that delivers answers as a plain text turn after a backend restart.

    `record` is a machine-readable record of which question round these answers belong to.
    Why it is in the prompt rather than the log: after a restart, the S3 pending record is deleted on the way
    out, so the transcript becomes the only durable trace of that mapping.
    """
    if _lang(language) == "en":
        return ("[Question answered] The user has answered the question you "
                "asked. Take these answers into account and carry on.\n"
                f"{lines}\n(answer record)\n{record}")
    return ("[질문 답변] 앞서 드린 질문에 사용자가 답했습니다. "
            "이 답변을 반영해 이어서 진행해 주세요.\n"
            f"{lines}\n(답변 기록)\n{record}")
