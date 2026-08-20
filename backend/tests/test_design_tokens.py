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

from pathlib import Path

from pathfinder.design_profile import ALLOWED_TOKENS, parse_design_md
from pathfinder.design_tokens import extract_tokens, inject_fence, render_fence

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


GOOD_REPLY = """문서에서 역할이 명시된 값만 골랐습니다.

```tokens
primary: #00754a
primary_foreground: #ffffff
background: #f2f0eb
destructive: #c82014
radius: 0.75rem
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
