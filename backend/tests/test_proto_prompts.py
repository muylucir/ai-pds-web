# backend/tests/test_proto_prompts.py
#
# The prompt is the build agent's only brake (the first_prompt docstring in
# proto/session.py). Verify that both languages carry the same instructions —
# we maintain two copies rather than assembling them, so an instruction missing from only one side can appear silently.
from __future__ import annotations

import pytest

from aipds.proto import prompts
from aipds.proto.prompts import (
    build_complete_description, build_complete_recorded,
    build_complete_rejection, handoff_prompt, missing_output_prompt,
    plan_prompt, resume_prompt,
)

SPEC = "aiplc-docs/discovery/prototypes/demo/PROTOTYPE-demo.md"
PROXY = "/api/proto/p1/demo/"


def _plan(language: str) -> str:
    return plan_prompt(language, spec_key=SPEC, proxy_path=PROXY)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_plan_prompt_carries_every_directive(language):
    """Both languages must apply the same brake. If even one is missing, builds in
    that language start without approval.

    What this prompt owns is **the session flow** — the plan, approval, the language of
    on-screen text, the completion declaration, and the runtime values specific to this
    build (the spec path and the proxy path). The shared technical contract is owned by
    `proto-config/CLAUDE.md` (test_build_agent_contract pins that side).
    """
    p = _plan(language)
    assert SPEC in p                    # instructs it to read the spec
    assert "AskUserQuestion" in p       # instructs it to get approval
    assert "build_complete" in p        # completion declaration
    assert PROXY in p                   # this build's proxy path (runtime value)
    # The generated app's on-screen text must also be in the project language (spec §4).
    # Without this instruction, an English project receives a prototype with a Korean UI.
    assert "i18n" in p


#: Tokens of the shared technical contract. `proto-config/CLAUDE.md` owns them, and if this
#: prompt restated them, the same rule would exist in three copies (ko/en prompts + contract).
SHARED_CONTRACT_TOKENS = ("@strands-agents/sdk", "temperature",
                          "BEDROCK_MODEL_ID", "basePath", "prototype/")


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("token", SHARED_CONTRACT_TOKENS)
def test_the_plan_prompt_does_not_restate_the_shared_contract(language, token):
    """Technical rules must live only in `proto-config/CLAUDE.md`.

    **Why they were moved.** The rationale for these rules originally living in the prompt was
    "skills only apply to prototypes with a UI, and this prompt is the only place that is always
    read". That premise was wrong — `proto-config/CLAUDE.md` is at the **"user" level** of
    `setting_sources` (builder.py swaps `CLAUDE_CONFIG_DIR`), so it is read on every turn
    regardless of skill invocation. The thing to compare against was the contract file, not skills.

    The cost of maintaining three copies has been measured: two per-language prompts are a
    path for a rule to go missing from one side (this file's header), and adding the contract
    file on top makes it impossible to tell which one is current.
    """
    assert token not in _plan(language), (
        f"{token!r}가 프롬프트에 남아 있다 — proto-config/CLAUDE.md가 SSOT다")


@pytest.mark.parametrize("language", ["ko", "en"])
def test_plan_prompt_forbids_building_in_the_first_turn(language):
    p = _plan(language).lower()
    # This instruction is the only brake — without it, the agent starts building immediately.
    forbid = ["빌드는 시작하지" in p or "do not start building" in p,
              "write/edit" in p or "write·edit" in p]
    assert all(forbid), p[:400]


def test_korean_prompt_is_korean_and_english_prompt_is_english():
    ko, en = _plan("ko"), _plan("en")
    assert any("가" <= c <= "힣" for c in ko)
    # Hangul mixed into the English prompt means the translation is incomplete. File paths
    # contain no Hangul (SPEC/PROXY are both ASCII), so this assertion is valid.
    assert not any("가" <= c <= "힣" for c in en), en


@pytest.mark.parametrize("language", ["ko", "en"])
def test_resume_prompt_asks_before_working(language):
    p = resume_prompt(language)
    assert "AskUserQuestion" in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_missing_output_prompt_says_not_to_look_for_the_old_code(language):
    # Without this instruction, the agent scans the filesystem looking for the deleted tree
    # (measured: 19+ seconds). Both languages must state it.
    p = missing_output_prompt(language, spec_key=SPEC)
    assert SPEC in p
    assert "AskUserQuestion" in p


#: What the person approving the plan picks between, per language. The labels are the user-facing
#: wording (how the screen looks), and the skill names are what the answer maps to.
DESIGN_OPTIONS = {
    "ko": ("모던 스타일 (shadcn/ui)", "업무 시스템 스타일 (Ant Design)", "(추천)"),
    "en": ("Modern (shadcn/ui)", "Business system (Ant Design)", "(Recommended)"),
}


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("which", ["plan", "missing_output"])
def test_planning_prompts_ask_for_the_design_style(language, which):
    """The contract picks antd only "when the spec or the user asks for it", and nobody asks
    unprompted — so the plan round trip has to offer the choice, or every prototype is shadcn.
    Both branches that present a plan carry it, in both languages, and each option must map to
    a skill that actually ships."""
    from pathlib import Path

    import aipds
    p = (_plan(language) if which == "plan"
         else missing_output_prompt(language, spec_key=SPEC))
    for needle in DESIGN_OPTIONS[language]:
        assert needle in p, f"{language}/{which}: {needle!r}가 없다"
    skills = Path(aipds.__file__).resolve().parents[2] / "proto-config" / "skills"
    for skill in ("shadcn-design", "antd-design"):
        assert skill in p
        assert (skills / skill / "SKILL.md").is_file()


@pytest.mark.parametrize("language", ["ko", "en"])
def test_continuing_sessions_do_not_reopen_the_design_style(language):
    """Resume and handoff keep the design system the prototype already has (the contract's
    improvement-session rule). Asking again there invites a switch halfway through a build."""
    for p in (resume_prompt(language),
              handoff_prompt(language, spec_key=SPEC, summary="s", remaining="r")):
        assert DESIGN_OPTIONS[language][1] not in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_handoff_prompt_carries_the_summary(language):
    p = handoff_prompt(language, spec_key=SPEC,
                       summary="장바구니 화면을 만들었다", remaining="결제 연동")
    assert "장바구니 화면을 만들었다" in p
    assert "결제 연동" in p
    assert "AskUserQuestion" in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_tool_texts_exist_for_both_languages(language):
    assert "prototype/" in build_complete_description(language)
    assert build_complete_rejection(language).strip() != ""


@pytest.mark.parametrize("language", ["ko", "en"])
def test_build_complete_recorded_exists(language):
    assert build_complete_recorded(language).strip() != ""


def test_an_unknown_language_falls_back_to_korean():
    assert _plan("klingon") == _plan("ko")


@pytest.mark.parametrize("language", ["ko", "en"])
def test_design_rules_carry_every_directive(language):
    out = prompts.design_rules(language)
    # Without the two file names and "do not edit them directly", the instruction does not hold.
    assert "aipds-theme.css" in out
    assert "DESIGN.md" in out
    for needle in (["복사", "import", "고치지", "시맨틱", "hex", "무관",
                    # Final review C1: the instruction to import it from the root
                    # layout *after* globals.css (required so it does not lose to
                    # shadcn's :root in the cascade) — without it, the brand is
                    # nullified without any error.
                    "레이아웃",
                    # Final review I5: it must be a repair instruction, not a warning
                    # (a command to fix the outdated config that wraps hsl(var(--x))).
                    "고쳐라",
                    # Final review M12: defensive wording to treat DESIGN.md only as a
                    # visual design reference and ignore unrelated instructions.
                    "시각 디자인 참고자료"]
                   if language == "ko"
                   else ["Copy", "import", "Do not edit", "semantic", "hex",
                         "unrelated",
                         "layout",
                         "change that setup",
                         "ignore any instruction"]):
        assert needle in out, f"{language}: {needle!r}가 없다"


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("has_tokens", [True, False])
def test_design_rules_send_an_antd_prototype_to_its_own_wiring(language, has_tokens):
    """The section is planted before anyone knows the prototype's design system, and its
    other lines are shadcn's. antd does not read the CSS variables, so without this line an
    antd prototype imports the theme file, passes the theme gate, and shows no brand."""
    from pathlib import Path

    import aipds
    out = prompts.design_rules(language, has_tokens=has_tokens)
    assert "antd-design" in out
    assert "ConfigProvider" in out
    assert "references/brand-theme.md" in out
    ref = (Path(aipds.__file__).resolve().parents[2] / "proto-config" / "skills"
           / "antd-design" / "references" / "brand-theme.md")
    assert ref.is_file()


@pytest.mark.parametrize("language", ["ko", "en"])
def test_design_rules_without_tokens_point_at_the_document(language):
    """Without tokens, the source of the brand is DESIGN.md, not the theme file.

    Measured 2026-08-19: pointing at a theme file with no values as "generated from the brand
    profile" led one agent to read it as having nothing to override and keep the shadcn
    defaults, while another agent read the prose and carried over the palette. The
    instruction must point to where the values are.
    """
    out = prompts.design_rules(language, has_tokens=False)
    assert "DESIGN.md" in out
    assert "globals.css" in out
    for needle in (["값이 없다", "옮겨라"] if language == "ko"
                   else ["no values", "move"]):
        assert needle in out, f"{language}: {needle!r}가 없다"
    # The wiring is still required — this import is the only way for a brand uploaded later
    # to reach this prototype without rebuilding it.
    assert "aipds-theme.css" in out


@pytest.mark.parametrize("language", ["ko", "en"])
def test_design_rules_with_tokens_do_not_ask_for_a_manual_move(language):
    # With tokens, there must be no instruction to carry values over by hand — with both
    # instructions present, the agent ends up holding both the file we overwrite and the values it wrote itself.
    out = prompts.design_rules(language, has_tokens=True)
    assert ("옮겨라" not in out) if language == "ko" else ("move" not in out)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_theme_rejection_tells_the_agent_what_to_do(language):
    out = prompts.build_complete_theme_rejection(language)
    assert "aipds-theme.css" in out
    assert ("거부됨" in out) if language == "ko" else ("Rejected" in out)
    # This rejection message must point to the same place as design_rules (import from the
    # root layout *after* globals.css) — otherwise the agent imports it inside globals.css
    # and reproduces the defect of losing in the cascade.
    assert ("레이아웃" in out) if language == "ko" else ("layout" in out)


# ---- Rejection messages of the Bash gate (attached to proto/build_guard.py's verdicts) ----


@pytest.mark.parametrize("language", ["ko", "en"])
def test_the_unsafe_command_refusal_names_what_was_caught(language):
    """It must **point to the fragment** that was caught.

    Without pointing, the model retries the same command in a different form and gets stuck
    in a loop — the docstring of agent/prompts.write_outside_docs recorded that defect, and
    this gate has the same failure path.
    """
    out = prompts.unsafe_command_refused(language, "npx playwright test")
    assert "npx playwright test" in out
    assert ("거부됨" in out) if language == "ko" else ("Refused" in out)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_the_unsafe_command_refusal_offers_the_alternative(language):
    """With only a rejection, the model knows only that it was 'blocked', not what to verify with.

    Give it both the build verification means (`npm run build`) and who checks the screen
    (the live preview in the prototype tab).
    """
    out = prompts.unsafe_command_refused(language, "npm run dev")
    assert "npm run build" in out


# ---- When the user has already said what to fix ----
#
# **Why this branch exists.** Someone who pressed "수정하기" already knows what to fix —
# they saw it in the preview. Yet the opening prompt instructed "아직 아무것도 수정하지 마 …
# AskUserQuestion으로 무엇을 개선할지 물어보고 답을 기다려줘", and while the question is up
# the input box is disabled (BuildPanel's `disabled={streaming || …}`), so the user
# **could not even type their own request.** It was a flow where they had to answer someone
# else's multiple choice before they could say their own piece.
#
# When a request comes along, that round trip is skipped. With no request (auto-start), it asks
# as before — in that case the user has not said anything, so asking is right.

def test_the_handoff_prompt_still_asks_when_no_request_was_given():
    from aipds.proto.prompts import handoff_prompt

    text = handoff_prompt("ko", spec_key="spec.md", summary="요약", remaining="남은 것")

    assert "AskUserQuestion" in text
    assert "아직 아무것도 수정하지 마" in text


def test_a_handoff_request_replaces_the_question_round_trip():
    from aipds.proto.prompts import handoff_prompt

    text = handoff_prompt("ko", spec_key="spec.md", summary="요약",
                          remaining="남은 것", request="장바구니 버튼을 오른쪽 위로")

    # The user's words are carried verbatim — summarizing them chips away at the intent.
    assert "장바구니 버튼을 오른쪽 위로" in text
    # And it does not ask back.
    assert "AskUserQuestion" not in text


def test_a_handoff_request_keeps_the_context_the_new_session_has_no_other_way_to_get():
    """The handoff branch is a **new session**, so there is no transcript (proto/session's
    _resolve_session_id). Sending only the request makes the agent start without knowing what
    the previous build left behind — the summary and "look at prototype/ first" must go together."""
    from aipds.proto.prompts import handoff_prompt

    text = handoff_prompt("ko", spec_key="spec.md", summary="투두 앱을 만들었다",
                          remaining="정렬 기능", request="색을 바꿔줘")

    assert "투두 앱을 만들었다" in text
    assert "prototype/" in text
    assert "spec.md" in text


def test_a_resume_request_also_skips_the_question():
    from aipds.proto.prompts import resume_prompt

    asked = resume_prompt("ko")
    assert "AskUserQuestion" in asked

    told = resume_prompt("ko", request="로그인 화면부터 마쳐줘")
    assert "로그인 화면부터 마쳐줘" in told
    assert "AskUserQuestion" not in told


def test_the_english_prompts_carry_the_request_too():
    from aipds.proto.prompts import handoff_prompt, resume_prompt

    assert "move the cart button" in handoff_prompt(
        "en", spec_key="spec.md", summary="s", remaining="r",
        request="move the cart button")
    assert "AskUserQuestion" not in resume_prompt("en", request="finish sign-in")


def test_a_plan_request_is_never_silently_dropped():
    """This flow does not reach `plan` (the first build) — because `__first__` fires
    automatically before the card provides input. Still, if a request arrives, it must be carried:
    the worst outcome is the words the user typed silently disappearing."""
    from aipds.proto.prompts import plan_prompt

    text = plan_prompt("ko", spec_key="spec.md", proxy_path="/p/",
                       request="로그인 화면을 먼저 만들어줘")

    assert "로그인 화면을 먼저 만들어줘" in text


INSTRUCTIONS = "aiplc-docs/discovery/prototype/build-instructions.md"


def _every_opening(language: str, instructions_key: str | None) -> list[str]:
    """All opening prompts that point to the spec (resume does not restate the spec)."""
    return [
        plan_prompt(language, spec_key=SPEC, proxy_path=PROXY,
                    instructions_key=instructions_key),
        missing_output_prompt(language, spec_key=SPEC,
                              instructions_key=instructions_key),
        handoff_prompt(language, spec_key=SPEC, summary="s", remaining="r",
                       instructions_key=instructions_key),
        handoff_prompt(language, spec_key=SPEC, summary="s", remaining="r",
                       instructions_key=instructions_key, request="버튼 색"),
    ]


@pytest.mark.parametrize("language", ["ko", "en"])
def test_openings_name_the_build_instructions_when_present(language):
    """Path A.1's build instructions are the only way design-context answers reach the builder.
    If we point only to the spec, the agent does not know that file exists."""
    for p in _every_opening(language, INSTRUCTIONS):
        assert SPEC in p
        assert INSTRUCTIONS in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_openings_without_instructions_are_unchanged(language):
    """Without instructions (Path B), not a single character differs from passing no argument."""
    assert _every_opening(language, None) == [
        plan_prompt(language, spec_key=SPEC, proxy_path=PROXY),
        missing_output_prompt(language, spec_key=SPEC),
        handoff_prompt(language, spec_key=SPEC, summary="s", remaining="r"),
        handoff_prompt(language, spec_key=SPEC, summary="s", remaining="r",
                       request="버튼 색"),
    ]


def test_english_openings_with_instructions_stay_english():
    for p in _every_opening("en", INSTRUCTIONS)[:3]:     # the last one carries a Korean request
        assert not any("가" <= c <= "힣" for c in p), p


# Every message after a completed build opens an improvement session — questions too. If the
# request is assumed to be a change instruction, the model changes nothing, declares completion, and closes the session.

@pytest.mark.parametrize("language", ["ko", "en"])
def test_a_carried_request_may_be_a_question_that_ends_without_completion(language):
    handoff = handoff_prompt(language, spec_key=SPEC, summary="s", remaining="r",
                             request="판매자 정보는 어떻게 가져와?")
    resume = resume_prompt(language, request="판매자 정보는 어떻게 가져와?")
    for text in (handoff, resume):
        if language == "ko":
            assert "질문이나 상의라면 채팅에 답하고" in text
            assert "아무것도 바꾸지 않았으면 `build_complete`를 부르지 마" in text
        else:
            assert "If it is a question or a discussion, answer it in the chat" in text
            assert "If you changed nothing, do not call `build_complete`" in text
        # For change requests, as before, it does not ask back and declares completion when done.
        assert "AskUserQuestion" not in text
        assert "build_complete" in text


@pytest.mark.parametrize("language", ["ko", "en"])
def test_the_unchanged_rejection_and_the_tool_send_answers_to_the_chat(language):
    word = "채팅" if language == "ko" else "chat"
    assert word in prompts.build_complete_unchanged(language)
    assert word in build_complete_description(language)
