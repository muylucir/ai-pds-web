# backend/tests/test_proto_prompts.py
#
# 프롬프트는 빌드 에이전트의 유일한 브레이크다(proto/session.py의
# first_prompt docstring). 두 언어가 같은 지시를 담고 있는지 확인한다 —
# 조립이 아니라 두 벌을 유지하므로, 한쪽에만 빠진 지시가 조용히 생길 수 있다.
from __future__ import annotations

import pytest

from pathfinder.proto import prompts
from pathfinder.proto.prompts import (
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
    빌드는 승인 없이 시작되거나 산출물을 엉뚱한 곳에 둔다."""
    p = _plan(language)
    assert SPEC in p                    # 스펙을 읽으라고 지시
    assert "AskUserQuestion" in p       # 승인을 받으라고 지시
    assert "prototype/" in p            # 산출물 위치
    assert "build_complete" in p        # 완료 선언
    assert "BEDROCK_MODEL_ID" in p      # 모델 주입 이름
    assert "basePath" in p              # 하위 경로 서빙
    assert PROXY in p
    # 생성되는 앱의 화면 문구도 프로젝트 언어여야 한다(스펙 §4). 이 지시가
    # 없으면 영어 프로젝트가 한국어 UI의 프로토타입을 받는다.
    assert "i18n" in p


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
    assert "pathfinder-theme.css" in out
    assert "DESIGN.md" in out
    for needle in (["복사", "import", "고치지", "시맨틱", "hex", "무관"]
                   if language == "ko"
                   else ["Copy", "import", "Do not edit", "semantic", "hex",
                         "unrelated"]):
        assert needle in out, f"{language}: {needle!r}가 없다"


@pytest.mark.parametrize("language", ["ko", "en"])
def test_theme_rejection_tells_the_agent_what_to_do(language):
    out = prompts.build_complete_theme_rejection(language)
    assert "pathfinder-theme.css" in out
    assert ("거부됨" in out) if language == "ko" else ("Rejected" in out)
