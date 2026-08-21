# backend/aipds/agent/prompts.py -- two per-language versions of every text the
# Discovery agent **reads**.
#
# Everything here enters the model's context: MCP tool descriptions (injected with
# the tool list every turn), the refusal/confirmation strings tools return, and the
# prompts the driver builds. These are not screen strings -- UI text is owned by
# frontend/lib/i18n.
#
# **Why this file exists (the 2026-08-04 defect).** Spec
# `2026-08-03-bilingual-ko-en` §3 made `place_rules` the single source of the
# language directive, and that part worked exactly as intended (zero Hangul in the
# assembled en output). Yet workspace chat in projects that had chosen English kept
# running in Korean. These were the remaining paths: tool descriptions and tool
# return values were **not directives**, so they sat outside that spec's field of
# view -- but to the model they are Korean prompts read on every single turn.
#
# So this repo's test for language is not "is there a single language directive"
# but **"is every text entering the model's context in the project's language"**.
#
# **Two complete versions, never assembled from fragments.** `proto/prompts.py`
# records the same judgement: splitting a sentence into pieces and substituting
# makes it impossible to see in which language an instruction lost its force. The
# clearest case here was submit_document's ordering instruction, "write the file
# first, then call this" -- weaken it and the tool accepts a false declaration
# while the document panel shows an empty screen.
from __future__ import annotations

#: Supported languages. Must be the same set as workspace_rules._LANGUAGES.
_LANGUAGES = ("ko", "en")
_DEFAULT = "ko"


def _lang(language: str) -> str:
    """An unknown value falls back to the default: the create route validates it, so
    nothing else arrives through the normal path, and running in Korean beats
    running with no prompt at all because of a corrupted manifest (the same
    discipline as place_rules)."""
    return language if language in _LANGUAGES else _DEFAULT


# ---- The MCP tool description and return strings used to live here ----
#
# There were four: `submit_document_description` plus its three refusals (path
# escape, missing file, empty file). They went with the `submit_document` tool on
# 2026-08-21 -- Discovery has no custom tools left, so this space being empty is
# correct.
#
# What those refusals protected ("a tool cannot declare a document that does not
# exist") became an unrepresentable failure once the decision moved from the tool
# to the hook: a `document` event is derived **from the fact that a file was
# written** (agent/reconcile.document_events), so there is no way to announce a
# document that is not there. Only the empty-file condition remains, and it lives
# inside that function.


# ---- Texts the driver builds (agent/claude_driver.py) ----


def question_payload_rejected(language: str, reason: str) -> str:
    """The refusal returned to the model (PermissionResultDeny) when an
    AskUserQuestion payload cannot be turned into a form. The model reads it and
    has to call again."""
    if _lang(language) == "en":
        return (f"Cannot build the question form: {reason}\n"
                "Call AskUserQuestion again with at least one option per "
                "question.")
    return (f"질문을 만들 수 없다: {reason}\n"
            "각 질문에 옵션을 최소 1개 넣어 AskUserQuestion을 다시 호출해라.")


def answer_first(language: str) -> str:
    """The notice pushed to the chat when a new turn arrives while a question is
    still open.

    This is **conversation text**, not screen text: it stays in the chat as agent
    speech and enters the transcript, so it follows the project language rather
    than the UI language (lib/startMessage.ts and approvalMarker.ts record the same
    judgement).
    """
    if _lang(language) == "en":
        return ("Please answer the question that is still open — use the "
                "question form in the right-hand panel.")
    return ("진행 중인 질문에 먼저 답변해 주세요 — 우측 패널의 질문 폼을 "
            "이용하세요.")


def ask_user_question_denied(language: str) -> str:
    """The reason the model reads when AskUserQuestion is denied, plus what to do
    instead.

    **A denial, not a removal.** Simply dropping the interception would make the
    question vanish silently the moment the model called the tool -- absent from the
    screen and from the chat alike. A denial puts an alternative in the model's
    hands, so that hole never opens (`write_outside_docs` follows the same pattern,
    and its docstring records that a refusal alone sends the model into a loop of
    retrying with a different path).

    Why this tool is not used (measured 2026-08-17): rebuilding questions already
    written to a file as this tool's input mangled 15 of 19 questions (79%) -- 11
    cases of substituted Hangul characters, 4 answers lost to abbreviation. Reading
    the file verbatim removes that entire class of failure.
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
    """The note given to the model when a question file has `[Answer]:` tags but no
    readable question.

    It goes out as the PostToolUse hook's `additionalContext` -- **it does not stop
    the turn.** Stopping would deny the model any chance to read this note and fix
    the file, leaving the user stuck. Measured (2026-08-17): the model reads the
    note, rewrites the file within the same turn, and that rewrite fires the hook
    again and produces a correct card.

    **Why silence is wrong here.** The first version passed quietly, and that was
    only correct while AskUserQuestion survived as a fallback. Now that the tool is
    denied, a parse failure is the complete loss of the question -- which is what
    happened in sarang-hpt: the file was created, no card appeared, and the chat
    said nothing.

    It **names what to fix**. Given only a reason, the model rewrites the same file
    (`write_outside_docs` offers an alternative for the same reason).
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
    """The reason the model reads when the turn stops on a question file write.

    It goes out as the PostToolUse hook's `stopReason`. It enters the model's
    context, so it follows the project language.

    **Saying "do not ask them again" explicitly is the point.** The upstream rules
    tell the agent to ask the user after composing the questions; on this path
    AI-PDS puts the file itself on screen. Without being told that, the model
    rebuilds the same questions through AskUserQuestion on the next turn -- and that
    regeneration is exactly what mangled 79% of the questions on 2026-08-17.
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


# `_answer_lines` used to live here. It laid answers out as `- 1: A,B`, and that
# was the limit of the earlier fix (fe6a482): it did not expand option letters into
# labels, so the record differed from the live screen. Rendering is now owned in one
# place by `aipds/answer_summary.py` (the frontend's discrimination was folded in
# there too). This file only takes that result and wraps it in the instruction for
# the model.


def file_answers_recorded(language: str, path: str, summary: str) -> str:
    """The text of the turn that calls the agent back after answers were recorded into
    a question file.

    It picks up the turn `file_questions_stop` ended. This is a **new turn**, not a
    parked future being resumed, so this one sentence has to say everything about
    where to go back to -- hence naming the file (having several question files is
    normal; one measured project had nine).

    It is conversation text that stays as the user's bubble, so it follows the
    project language rather than the UI language (`answer_first` records the same
    judgement).

    **The answers are carried in the body (2026-08-21).** It used to name only the
    file, and the result was that refreshing the workspace and restoring the
    conversation **pinned every round's user bubble to this one sentence.** The live
    screen draws the real answers (the frontend's `answerSummary`), so the same round
    looked different before and after a refresh -- the defect recorded in
    `agent/answer_store.py`'s header, resurfacing on the file-question path.

    That side was fixed by joining records on `tool_use_id`. This path has no such
    tool call at all (what the agent calls is `Write`). So instead **the transcript
    itself is made to carry the truth** -- no join key, no new store, no ordering
    dependency.

    **Why not join on an opaque marker**: `frontend/lib/approvalMarker.ts` already
    wrote this class down: "this text is not a machine signal. That turn **stays as
    the user's bubble** in the transcript and in the chat history. The agent has to
    understand it and **a human has to read it**." What a person who answered
    questions would say is their answers, so carrying the answers runs with that
    principle rather than against it.

    **The file is still authoritative.** The values exist in two places (this
    sentence and the `[Answer]:` tags), but both are built from the same dict in the
    same request, so drift is impossible -- and the sentence names the file as the
    source of record, so what the agent re-reads does not change.

    **`summary` arrives already rendered (2026-08-21).** While this function laid
    the answers out itself, option letters stayed raw ("- 1: A,B") and the live
    screen expanded them into labels -- record and screen still differed. Rendering
    is now owned in one place by `aipds/answer_summary.py`, and **the frontend uses
    this same string** (routes/answers.py returns it alongside). One representation
    cannot diverge from itself.
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
    """The pointer appended to the resume turn when `aiplc-state.md` has no Current
    Stage.

    It is a **complete paragraph** concatenated after `file_answers_recorded`.
    Concatenating paragraphs rather than substituting fragments keeps this file's
    header rule ("two versions, each a complete text").

    **Why the backend does not write the file itself.** Only the agent knows the
    stage **name**. Guessing it from the path (`.../envision/xxx-questions.md` ->
    "Envision") would create a mapping that duplicates the ruleset's stage names --
    a second source of truth that goes quietly out of step the moment the ruleset is
    swapped.

    **It changed from a tool to a file on 2026-08-18.** The old wording told the
    agent to call `report_stage`. When that tool was replaced by the PostToolUse
    hook (agent/reconcile.py), the target of this pointer moved from a tool call to
    **a file write** -- and that is the behaviour the upstream rules ask for anyway
    (`common/workflow-changes.md`, `discovery/prototype-validation.md` Step 10).

    Why the pointer is still needed: the hook and the turn-boundary reconciliation
    move the file onto the screen **when it exists**. With no file there is nothing
    to move, and only the agent can create it.
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
    """The reason the model reads when the turn stops on a `build-instructions.md`
    write.

    It goes out as the PostToolUse hook's `stopReason` (the same path as
    `file_questions_stop`).

    **Naming the next action is the point.** The old `handoff_prototype` tool's
    success message already recorded this judgement: without it the agent either
    carries on into upstream Step 4 (Iterate) or asks for credentials -- both
    measured failures (keumkang-v5: credential check -> demand for an API key ->
    list of prerequisites, with the tab pointed at zero times).

    Turning the tool into a hook changed only **who guarantees the call happens**.
    What the wording has to carry is unchanged: this is where Discovery ends, the
    build happens in the tab, do not ask for credentials, and Steps 4-6 are deferred
    rather than abandoned.
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
    """The reason the model reads when the PreToolUse hook refuses a write outside
    `aiplc-docs/`.

    It **names the path** that was caught: without that, the model retries the same
    write with a different path and loops. And it offers an alternative -- given only
    a refusal the model knows it is blocked but not where the spec should go (see the
    defect recorded in agent/discovery_guard.py's header).

    **It does not pin a path.** An earlier version told the agent to write
    `prototypes/{slug}/PROTOTYPE-{slug}.md`, which is Path B's layout (use-case
    prioritisation, three of them). Path A.1 (derived from Envision, a single
    prototype) belongs at `prototype/prototype-spec.md` and has no slug
    (proto/layout.py's header). Pinning either one makes the instruction wrong for an
    agent on the other path, so this sends the agent back to the location its own
    stage rules define.
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
    """The reason given when a build command, a server start, or a file creation
    outside the workspace is refused."""
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
    """What the chat says when the CLI reports a failed turn
    (`ResultMessage.is_error`).

    It asks the user to try again because the common cause is transient: Bedrock
    429/529 under workshop load succeeds on the next attempt. The diagnostic
    (`api_error_status`) goes to the log -- an HTTP status is not something a
    workshop participant can act on.
    """
    if _lang(language) == "en":
        return ("This turn failed — please try again in a moment. If it keeps "
                "happening, let an administrator know.")
    return ("이번 턴이 실패했습니다 — 잠시 후 다시 시도해 주세요. "
            "반복되면 관리자에게 알려주세요.")


def answers_resumed(language: str, lines: str, record: str) -> str:
    """The prompt that delivers answers as a plain text turn after a backend restart.

    `record` is the machine-readable note of which question round these answers
    belong to. It is in the prompt rather than the log because the S3 pending record
    is deleted on the way out after a restart, which leaves the transcript as the
    only durable trace of that correspondence.
    """
    if _lang(language) == "en":
        return ("[Question answered] The user has answered the question you "
                "asked. Take these answers into account and carry on.\n"
                f"{lines}\n(answer record)\n{record}")
    return ("[질문 답변] 앞서 드린 질문에 사용자가 답했습니다. "
            "이 답변을 반영해 이어서 진행해 주세요.\n"
            f"{lines}\n(답변 기록)\n{record}")
