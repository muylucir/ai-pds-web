# backend/tests/test_design_tokens.py
#
# 산문뿐인 DESIGN.md에서 토큰을 뽑는 계층만 시험한다. 모델 호출은 주입된
# `async (prompt) -> str`이라 여기서는 가짜 호출자를 쓴다 — 라우트 배선은
# test_routes_design.py, 워크스페이스 반영은 test_proto_design_sync.py.
#
# 이 파일의 규율 하나: **모델 응답을 파싱하는 코드는 없어야 한다.** 응답은
# design_profile.parse_design_md를 그대로 지나야 하고, 그래서 여기 단정들은
# 파서의 계약(줄 번호 오류, 화이트리스트)에 기대고 있다.
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aipds.design_profile import ALLOWED_TOKENS, parse_design_md
from aipds.design_tokens import (_prompt, extract_tokens, has_fence,
                                      inject_fence, render_fence)

FIXTURE = Path(__file__).parent / "fixtures" / "design-no-fence.md"
NO_FENCE = FIXTURE.read_text(encoding="utf-8")

WITH_FENCE = """# ACME

```tokens
primary: #5b2ea6
radius: 0.75rem
```

## 톤
여백을 넉넉히.
"""


class FakeCaller:
    """응답을 순서대로 돌려주고 받은 프롬프트를 기록한다."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # 응답이 모자라면 마지막 것을 반복한다 — "계속 실패하는 모델"을 쓰기 쉽게.
        return self._replies[min(len(self.prompts) - 1, len(self._replies) - 1)]


#: `design-no-fence.md`에 대해 **규칙을 지킨** 응답. 배관 시험용 가짜 응답이지만
#: 프롬프트가 지시하는 답이어야 한다 — 어긋나면 "좋은 답"이 규칙 위반을 뜻하게 되고,
#: 그 상태를 아무 테스트도 잡지 않는다(실제로 그랬다).
#:
#: `radius`가 **없는** 것이 그 규칙이다: 그 문서는 카드 12px / 버튼 50px /
#: 아바타 50%로 한 역할에 값이 셋이고, 문서가 일반 규칙을 고르지 않았으므로 omit이
#: 정직한 답이다. `foreground`도 없다 — 본문 색이 `rgba()`라 허용 형식 밖이다.
GOOD_REPLY = """문서에서 역할이 명시된 값만 골랐습니다.

```tokens
primary: #00754a
primary_foreground: #ffffff
background: #f2f0eb
destructive: #c82014
font_sans: Inter
```
"""


async def test_existing_fence_wins_without_calling_the_model():
    caller = FakeCaller(GOOD_REPLY)
    tokens, warnings = await extract_tokens(WITH_FENCE, caller)
    assert tokens == {"primary": "#5b2ea6", "radius": "0.75rem"}
    assert warnings == []
    # 펜스가 권위다 — 손으로 쓴 관리자의 값에 모델을 끼워넣지 않는다.
    assert caller.prompts == []


async def test_extracts_tokens_from_a_prose_only_document():
    caller = FakeCaller(GOOD_REPLY)
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens["primary"] == "#00754a"
    assert tokens["font_sans"] == "Inter"
    assert warnings == []
    assert len(caller.prompts) == 1
    # 문서를 실제로 보냈는지 — 프롬프트만 보내고 문서를 빼먹는 실수를 잡는다.
    assert "Riverbank Green" in caller.prompts[0]


async def test_reply_without_a_fence_is_not_silently_accepted():
    # parse_design_md는 펜스가 없으면 ({}, 전체 산문)을 조용히 돌려준다. 그
    # 반환을 그대로 믿으면 "추출 성공, 토큰 0개"가 되어 지금 고치는 버그가
    # 그대로 재현된다.
    caller = FakeCaller("토큰을 찾지 못했습니다.")
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens == {}
    assert warnings
    assert len(caller.prompts) == 2  # 한 번 되묻는다


async def test_no_caller_at_all_is_a_warning_not_an_exception():
    # 배포에 ANTHROPIC_MODEL이 없으면 호출자가 없다. 그때도 업로드는 계속돼야
    # 한다 — 산문만 적용하는 것도 유효한 상태다.
    tokens, warnings = await extract_tokens(NO_FENCE, None)
    assert tokens == {}
    assert warnings


async def test_no_caller_still_reads_an_existing_fence():
    tokens, warnings = await extract_tokens(WITH_FENCE, None)
    assert tokens == {"primary": "#5b2ea6", "radius": "0.75rem"}
    assert warnings == []


async def test_invalid_value_is_retried_once_with_the_parser_message():
    bad = "```tokens\nprimary: rebeccapurple\n```\n"
    caller = FakeCaller(bad, GOOD_REPLY)
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens["primary"] == "#00754a"
    assert warnings == []
    assert len(caller.prompts) == 2
    # 되묻는 프롬프트는 파서가 짚어준 줄 번호를 그대로 싣는다 — 모델이 스스로
    # 고칠 수 있는 유일한 근거다.
    assert "line 2" in caller.prompts[1]
    assert "rebeccapurple" in caller.prompts[1]


async def test_gives_up_after_one_retry_and_warns():
    caller = FakeCaller("```tokens\nradius: 12\n```\n")
    tokens, warnings = await extract_tokens(NO_FENCE, caller)
    assert tokens == {}
    assert warnings
    assert len(caller.prompts) == 2  # 두 번까지만 — 업로드를 붙잡지 않는다


async def test_key_outside_the_whitelist_never_reaches_the_profile():
    caller = FakeCaller("```tokens\nbrand_color: #00754a\n```\n")
    tokens, _ = await extract_tokens(NO_FENCE, caller)
    assert tokens == {}


async def test_extracted_keys_are_all_allowed():
    caller = FakeCaller(GOOD_REPLY)
    tokens, _ = await extract_tokens(NO_FENCE, caller)
    assert set(tokens) <= set(ALLOWED_TOKENS)


def test_render_fence_round_trips_through_the_parser():
    tokens = {"primary": "#00754a", "radius": "0.75rem", "font_sans": "Inter"}
    parsed, prose = parse_design_md(render_fence(tokens))
    assert parsed == tokens
    assert prose == ""


def test_inject_fence_puts_the_block_after_the_first_heading():
    tokens = {"primary": "#00754a"}
    injected = inject_fence(NO_FENCE, tokens)
    lines = injected.split("\n")
    assert lines[0].startswith("# Design System")
    assert "```tokens" in "\n".join(lines[1:6])
    # 주입된 문서는 이제 사람이 쓴 것과 구분되지 않는다.
    assert parse_design_md(injected)[0] == tokens


def test_inject_fence_leaves_the_prose_untouched():
    before = parse_design_md(NO_FENCE)[1]
    after = parse_design_md(inject_fence(NO_FENCE, {"primary": "#00754a"}))[1]
    assert after == before


def test_inject_fence_without_a_heading_puts_the_block_first():
    injected = inject_fence("톤을 지켜라.\n", {"primary": "#00754a"})
    assert injected.startswith("```tokens")
    assert "톤을 지켜라." in parse_design_md(injected)[1]


# ---- 프롬프트가 한 문서에 편향되지 않았는지 ----
#
# 판단 규칙 5개 중 4개가 `design-no-fence.md`에서 그대로 나왔고, 그중 하나는 그
# 문서의 색 값(`rgba(0,0,0,0.87)`)을 리터럴로 싣고 있었다. 튜닝한 문서와 검증한
# 문서가 같으면 일반화한다는 증거가 없고, 이 기능의 실패는 조용하고 전역이다 —
# `primary`나 `foreground`가 어긋나면 모든 프로젝트의 모든 프로토타입이 그 색으로
# 나간다.
#
# 아래 검사는 추출 **품질**을 재지 않는다(모델이 없다). 재는 것은 지시가
# 문서-특정적이지 않다는 것 하나이고, 그것이 코드로 지킬 수 있는 부분이다.
#
# **품질은 실물 모델로 한 번 확인했다(2026-08-20, global.anthropic.claude-opus-5,
# 세 픽스처).** 세 문서가 서로 다른 omission을 서로 다른 이유로 냈다:
#
#   design-no-fence.md          foreground·radius omit — 본문이 rgba(), radius 셋
#   design-primary-labelled.md  foreground·radius 추출 — 본문이 hex, radius 하나
#   design-token-table.md       oklch() 전부 omit, 스택의 첫 서체만
#
# 마지막 줄이 이 커밋의 요점이다: 거부 표기를 열거하던 옛 프롬프트(`rgba`/`hsl`)는
# `oklch()`를 목록에 갖고 있지 않았다. 허용 형식 밖은 전부 omit으로 닫아야 문서
# 모양이 달라도 성립한다.
#
# 그 확인을 CI에 넣지 않는다 — 실물 호출은 비결정적이고 비용이 붙는다. 재현하려면
# 픽스처를 `extract_tokens`에 실물 호출자와 함께 넣으면 된다
# (`app.design_token_extractor`가 그 호출자를 만드는 모양이다).

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("design-*.md"))

#: 어느 픽스처에서든 나온 값. 프롬프트에 이것이 있으면 그 문서에 맞춰 쓴 것이다.
_DOCUMENT_VALUES = ("rgba(0,0,0,0.87)", "SoDoSans", "JetBrains Mono", "Inter",
                    "Riverbank", "Harbour Blue", "oklch(", "Source Sans")

_HEX_VALUE = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")


def test_the_corpus_has_more_than_one_document_shape():
    """픽스처가 하나면 그것이 곧 사양이 된다."""
    assert len(FIXTURES) >= 3, [p.name for p in FIXTURES]


@pytest.mark.parametrize("needle", _DOCUMENT_VALUES)
def test_the_prompt_carries_no_value_from_any_document(needle):
    """지시에 특정 문서의 값이 박히면 모델이 그 값을 패턴으로 찾는다."""
    assert needle not in _prompt("(document)"), (
        f"{needle!r}가 프롬프트에 있다 — 부류로 말해야 한다")


def test_the_prompt_states_no_concrete_colour():
    """`#rrggbb`·`#rgb`는 **형식 자리표**이므로 남는다(hex 숫자가 아니다).
    실제 hex 값이 있으면 예시가 아니라 답을 심는 것이다."""
    found = _HEX_VALUE.findall(_prompt("(document)"))
    assert found == [], found


def test_the_prompt_does_not_hardcode_a_role_resolution():
    """`follow the ROLE the document gives them`과 모순되던 자리다.

    "헤딩 색과 CTA 색이 다르면 CTA가 primary"는 한 문서의 관례다. 많은 디자인
    시스템이 브랜드/헤딩 색을 명시적으로 `Primary`라 부르고 버튼에는 다른 색을
    쓴다(`design-primary-labelled.md`가 그 반대 경우다) — 그 문서에서는 이 문장이
    문서의 라벨을 무시하라는 지시가 된다.
    """
    # 공백을 정규화해서 본다 — 원문은 줄바꿈이 끼어 있어 리터럴 비교로는
    # "the CTA colour is"가 잡히지 않았다(이 검사가 조용히 통과했던 이유다).
    prompt = " ".join(_prompt("(document)").split())
    assert "CTA colour is" not in prompt
    assert "ROLE the document gives" in prompt, "일반 규칙은 남아 있어야 한다"


def test_the_prompt_closes_the_world_instead_of_listing_rejects():
    """거부할 표기를 열거하면 빠진 표기가 통과한다.

    `oklch()`·`color-mix()`·`lab()`·`var(--x)`는 `rgba`/`hsl`과 같은 부류인데
    열거에는 없었다(`design-token-table.md`가 `oklch()` 문서다). 허용 형식은 이미
    화이트리스트에서 생성되므로(`_value_rules`), 그 밖은 전부 omit이라고 닫으면
    목록에 구멍이 생길 수 없다.
    """
    prompt = _prompt("(document)")
    for enumerated in ("rgba", "hsl"):
        assert enumerated not in prompt, (
            f"{enumerated!r}를 열거한다 — 허용 형식 밖은 전부 omit이어야 한다")


def test_the_radius_rule_is_decidable():
    """예전 문구는 "cards와 buttons 중 하나가 dominates하면 그것"이었다.

    dominance가 정의되지 않아 판정할 수 없고, `GOOD_REPLY`가 그 규칙을 어기는
    답(카드 반경)을 "좋은 답"으로 싣고 있었다 — 규칙과 기대가 어긋난 채 아무
    테스트도 잡지 않았다.
    """
    assert "dominates" not in _prompt("(document)")


# ---- 문서 모양이 달라도 배관이 성립하는지 ----


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_every_fixture_is_a_no_fence_document(path):
    """이 코퍼스의 정의다 — 펜스가 있으면 추출 경로를 타지 않는다."""
    assert not has_fence(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_inject_fence_round_trips_on_every_shape(path):
    """산문은 한 바이트도 바뀌지 않고, 심은 토큰은 되읽힌다.

    프런트매터로 시작하는 문서(`design-token-table.md`)가 이 검사의 이유다 —
    `inject_fence`는 "첫 `#` 줄"을 찾으므로 헤딩이 첫 줄이 아닌 모양에서 블록이
    어디로 가는지가 문서 모양에 따라 달라진다.
    """
    original = path.read_text(encoding="utf-8")
    tokens = {"primary": "#1b4f8a", "radius": "4px"}
    injected = inject_fence(original, tokens)

    read_tokens, prose = parse_design_md(injected)
    assert read_tokens == tokens
    assert prose == original.replace("\r\n", "\n").rstrip("\n")
