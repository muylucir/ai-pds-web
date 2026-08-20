# backend/tests/test_proto_prompts.py
#
# 프롬프트는 빌드 에이전트의 유일한 브레이크다(proto/session.py의
# first_prompt docstring). 두 언어가 같은 지시를 담고 있는지 확인한다 —
# 조립이 아니라 두 벌을 유지하므로, 한쪽에만 빠진 지시가 조용히 생길 수 있다.
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
    """두 언어 모두 같은 브레이크를 걸어야 한다. 하나라도 빠지면 그 언어의
    빌드는 승인 없이 시작된다.

    이 프롬프트가 소유하는 것은 **세션의 흐름**이다 — 계획, 승인, 화면 문구의
    언어, 완료 선언, 그리고 이 빌드에만 해당하는 런타임 값(스펙 경로와 프록시
    경로). 공통 기술 계약은 `proto-config/CLAUDE.md`가 소유한다
    (test_build_agent_contract가 그쪽을 고정한다).
    """
    p = _plan(language)
    assert SPEC in p                    # 스펙을 읽으라고 지시
    assert "AskUserQuestion" in p       # 승인을 받으라고 지시
    assert "build_complete" in p        # 완료 선언
    assert PROXY in p                   # 이 빌드의 프록시 경로(런타임 값)
    # 생성되는 앱의 화면 문구도 프로젝트 언어여야 한다(스펙 §4). 이 지시가
    # 없으면 영어 프로젝트가 한국어 UI의 프로토타입을 받는다.
    assert "i18n" in p


#: 공통 기술 계약의 토큰. `proto-config/CLAUDE.md`가 소유하며, 이 프롬프트가
#: 다시 말하면 같은 규칙이 세 벌(ko/en 프롬프트 + 계약)이 된다.
SHARED_CONTRACT_TOKENS = ("@strands-agents/sdk", "temperature",
                          "BEDROCK_MODEL_ID", "basePath", "prototype/")


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("token", SHARED_CONTRACT_TOKENS)
def test_the_plan_prompt_does_not_restate_the_shared_contract(language, token):
    """기술 규칙은 `proto-config/CLAUDE.md`에만 있어야 한다.

    **왜 옮겼는가.** 이 규칙들이 원래 프롬프트에 있었던 근거는 "스킬은 UI가 있는
    프로토타입에만 걸리고, 이 프롬프트가 유일하게 항상 읽히는 자리다"였다. 그
    전제가 틀렸다 — `proto-config/CLAUDE.md`는 `setting_sources`의 **"user"
    레벨**이므로(builder.py가 `CLAUDE_CONFIG_DIR`을 갈아끼운다) 스킬 호출과
    무관하게 매 턴 읽힌다. 비교 대상은 스킬이 아니라 계약 파일이었다.

    세 벌을 유지하는 대가는 실측돼 있다: 언어별 프롬프트 2벌은 한쪽에만 규칙이
    빠지는 경로이고(이 파일 헤더), 거기에 계약 파일까지 더하면 어느 쪽이 최신인지
    알 수 없다.
    """
    assert token not in _plan(language), (
        f"{token!r}가 프롬프트에 남아 있다 — proto-config/CLAUDE.md가 SSOT다")


@pytest.mark.parametrize("language", ["ko", "en"])
def test_plan_prompt_forbids_building_in_the_first_turn(language):
    p = _plan(language).lower()
    # 이 지시가 유일한 브레이크다 — 없으면 에이전트가 바로 빌드를 시작한다.
    forbid = ["빌드는 시작하지" in p or "do not start building" in p,
              "write/edit" in p or "write·edit" in p]
    assert all(forbid), p[:400]


def test_korean_prompt_is_korean_and_english_prompt_is_english():
    ko, en = _plan("ko"), _plan("en")
    assert any("가" <= c <= "힣" for c in ko)
    # 영어 프롬프트에 한글이 섞이면 번역이 덜 된 것이다. 파일 경로에는 한글이
    # 없으므로(SPEC/PROXY 모두 ASCII) 이 단정이 유효하다.
    assert not any("가" <= c <= "힣" for c in en), en


@pytest.mark.parametrize("language", ["ko", "en"])
def test_resume_prompt_asks_before_working(language):
    p = resume_prompt(language)
    assert "AskUserQuestion" in p


@pytest.mark.parametrize("language", ["ko", "en"])
def test_missing_output_prompt_says_not_to_look_for_the_old_code(language):
    # 이 지시가 없으면 에이전트가 삭제된 트리를 찾아 파일시스템을 훑는다
    # (실측: 19초 이상). 두 언어 모두 명시해야 한다.
    p = missing_output_prompt(language, spec_key=SPEC)
    assert SPEC in p
    assert "AskUserQuestion" in p


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
    # 파일 이름 둘과 "직접 고치지 마라"가 빠지면 지시가 성립하지 않는다.
    assert "aipds-theme.css" in out
    assert "DESIGN.md" in out
    for needle in (["복사", "import", "고치지", "시맨틱", "hex", "무관",
                    # 최종 리뷰 C1: globals.css *다음에* 루트 레이아웃에서
                    # import하라는 지시(캐스케이드에서 shadcn의 :root에
                    # 지지 않으려면 필수) — 없으면 브랜드가 오류 없이
                    # 무효화된다.
                    "레이아웃",
                    # 최종 리뷰 I5: 경고가 아니라 수리 지시여야 한다
                    # (hsl(var(--x))를 감싸는 구식 설정을 고치라는 명령).
                    "고쳐라",
                    # 최종 리뷰 M12: DESIGN.md를 시각 디자인 참고자료로만
                    # 다루고 무관한 지시는 무시하라는 방어적 문구.
                    "시각 디자인 참고자료"]
                   if language == "ko"
                   else ["Copy", "import", "Do not edit", "semantic", "hex",
                         "unrelated",
                         "layout",
                         "change that setup",
                         "ignore any instruction"]):
        assert needle in out, f"{language}: {needle!r}가 없다"


@pytest.mark.parametrize("language", ["ko", "en"])
def test_design_rules_without_tokens_point_at_the_document(language):
    """토큰이 없으면 브랜드의 출처는 테마 파일이 아니라 DESIGN.md다.

    2026-08-19 실측: 값이 없는 테마 파일을 "브랜드 프로필에서 생성됨"이라고
    가리킨 결과, 한 에이전트는 그 파일을 덮을 것이 없다고 읽고 shadcn 기본값을
    뒀고 다른 에이전트는 산문을 읽어 팔레트를 옮겼다. 지시가 값이 있는 곳을
    가리켜야 한다.
    """
    out = prompts.design_rules(language, has_tokens=False)
    assert "DESIGN.md" in out
    assert "globals.css" in out
    for needle in (["값이 없다", "옮겨라"] if language == "ko"
                   else ["no values", "move"]):
        assert needle in out, f"{language}: {needle!r}가 없다"
    # 배선은 여전히 요구한다 — 이 import가 나중에 올라올 브랜드를 다시 빌드하지
    # 않고 이 프로토타입에 닿게 하는 유일한 길이다.
    assert "aipds-theme.css" in out


@pytest.mark.parametrize("language", ["ko", "en"])
def test_design_rules_with_tokens_do_not_ask_for_a_manual_move(language):
    # 토큰이 있으면 값을 손으로 옮기라는 지시가 없어야 한다 — 두 지시가 함께
    # 있으면 에이전트가 우리가 덮어쓰는 파일과 자기가 쓴 값을 동시에 들고 있게 된다.
    out = prompts.design_rules(language, has_tokens=True)
    assert ("옮겨라" not in out) if language == "ko" else ("move" not in out)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_theme_rejection_tells_the_agent_what_to_do(language):
    out = prompts.build_complete_theme_rejection(language)
    assert "aipds-theme.css" in out
    assert ("거부됨" in out) if language == "ko" else ("Rejected" in out)
    # 이 거부 문구는 design_rules와 같은 곳(globals.css *다음에* 루트
    # 레이아웃에서 import)을 지목해야 한다 — 그렇지 않으면 에이전트가
    # globals.css 안에서 import해 캐스케이드에서 지는 결함을 재현한다.
    assert ("레이아웃" in out) if language == "ko" else ("layout" in out)


# ---- Bash 게이트의 거부 문구 (proto/build_guard.py의 판정에 붙는다) ----


@pytest.mark.parametrize("language", ["ko", "en"])
def test_the_unsafe_command_refusal_names_what_was_caught(language):
    """무엇이 걸렸는지 **조각으로 지목**해야 한다.

    지목이 없으면 모델이 같은 명령을 형태만 바꿔 재시도하며 루프에 빠진다 —
    agent/prompts.write_outside_docs의 docstring이 그 결함을 기록해 뒀고,
    이 게이트도 같은 실패 경로를 갖는다.
    """
    out = prompts.unsafe_command_refused(language, "npx playwright test")
    assert "npx playwright test" in out
    assert ("거부됨" in out) if language == "ko" else ("Refused" in out)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_the_unsafe_command_refusal_offers_the_alternative(language):
    """거부만 하면 모델은 '막혔다'만 알고 무엇으로 검증할지는 모른다.

    빌드 검증 수단(`npm run build`)과 화면 확인 주체(프로토타입 탭의 라이브
    프리뷰)를 함께 준다.
    """
    out = prompts.unsafe_command_refused(language, "npm run dev")
    assert "npm run build" in out
