# backend/tests/test_design_profile.py
#
# 프로필 도메인만 시험한다 — S3는 FakeS3Store, 라우트는 test_routes_design.py,
# 워크스페이스 반영은 test_proto_design_sync.py.
from __future__ import annotations

import json

import pytest

from pathfinder.design_profile import (
    ALLOWED_TOKENS, DESIGN_PROFILE_KEY, TEMPLATE_MD, DesignProfileError,
    DesignProfileStore, parse_design_md,
)
from tests.fakes.in_memory_s3 import FakeS3Store

GOOD_MD = """# ACME 브랜드

```tokens
primary: #5b2ea6
primary_foreground: #ffffff
radius: 0.75rem
font_sans: Pretendard
```

## 톤
여백을 넉넉히 쓰고, 강조는 색 대비가 아니라 크기로 만든다.
"""


def test_parses_tokens_and_keeps_the_rest_as_prose():
    tokens, prose = parse_design_md(GOOD_MD)
    assert tokens == {"primary": "#5b2ea6", "primary_foreground": "#ffffff",
                      "radius": "0.75rem", "font_sans": "Pretendard"}
    # 펜스 블록은 산문에서 빠지고, 나머지는 순서대로 남는다.
    assert "```tokens" not in prose
    assert prose.startswith("# ACME 브랜드")
    assert "여백을 넉넉히" in prose


def test_prose_only_file_is_valid():
    tokens, prose = parse_design_md("## 톤\n둥근 모서리를 쓴다.\n")
    assert tokens == {}
    assert "둥근 모서리" in prose


def test_unknown_token_is_rejected_with_its_line_number():
    md = "```tokens\nprimary: #fff\nbrand_color: #123456\n```\n"
    with pytest.raises(DesignProfileError) as exc:
        parse_design_md(md)
    assert exc.value.code == "invalid"
    assert "line 3" in str(exc.value)
    assert "brand_color" in str(exc.value)


@pytest.mark.parametrize("line,bad", [
    ("primary: rebeccapurple", "hex"),
    ("radius: 12", "length"),
    ("primary: #12345", "hex"),
])
def test_bad_values_are_rejected(line, bad):
    with pytest.raises(DesignProfileError) as exc:
        parse_design_md(f"```tokens\n{line}\n```\n")
    assert bad in str(exc.value)


def test_duplicate_key_is_rejected():
    with pytest.raises(DesignProfileError) as exc:
        parse_design_md("```tokens\nprimary: #fff\nprimary: #000\n```\n")
    assert "line 3" in str(exc.value) and "duplicate" in str(exc.value)


def test_unterminated_fence_is_rejected():
    with pytest.raises(DesignProfileError) as exc:
        parse_design_md("```tokens\nprimary: #fff\n")
    assert "line 1" in str(exc.value)


def test_crlf_and_bom_and_comments_are_tolerated():
    md = "\ufeff```tokens\r\n# 브랜드 색\r\nprimary: #5b2ea6\r\n\r\n```\r\n"
    tokens, _ = parse_design_md(md)
    assert tokens == {"primary": "#5b2ea6"}


def test_template_parses_and_the_worked_examples_are_real_tokens():
    """서식은 admin이 그대로 내려받아 채우는 것이므로 반드시 유효하게
    파싱돼야 한다. 최종 리뷰 I4: 예전에는 14개 토큰이 전부 주석 처리돼
    있어서 "값만 고치고 `#`을 지우지 않는" 가장 흔한 admin 실수가 오류 없이
    `{}`로 통과했다 -- 지금은 세 토큰을 `#`을 지운 예시로 내보내서, 서식을
    그대로 받은 admin이 "#만 지우면 반영된다"를 직접 보게 한다."""
    tokens, prose = parse_design_md(TEMPLATE_MD)
    assert tokens == {"primary": "#5b2ea6", "radius": "0.75rem",
                      "font_sans": "Pretendard"}
    assert prose.strip()
    # "줄 앞의 #을 지우세요" 지시 자체가 빠지면 이 수정의 요점이 사라진다.
    assert "지우세요" in TEMPLATE_MD and "앞의 `#`" in TEMPLATE_MD
    for key in ALLOWED_TOKENS:
        assert key in TEMPLATE_MD, f"서식에 {key} 안내가 없다"


@pytest.mark.asyncio
async def test_load_returns_none_when_absent():
    assert await DesignProfileStore(FakeS3Store()).load() is None


@pytest.mark.asyncio
async def test_save_persists_markdown_only_and_load_reparses():
    s3 = FakeS3Store()
    store = DesignProfileStore(s3)
    saved = await store.save(filename="acme.md", uploaded_by="admin@x",
                             markdown=GOOD_MD)
    assert saved.tokens["primary"] == "#5b2ea6"
    stored = json.loads(s3.blobs[DESIGN_PROFILE_KEY])
    # 파생값(tokens/prose)은 저장하지 않는다 — 파서를 고쳤을 때 낡지 않는다.
    assert set(stored) == {"filename", "uploaded_at", "uploaded_by", "markdown"}
    assert stored["markdown"] == GOOD_MD
    loaded = await store.load()
    assert loaded is not None
    assert loaded.tokens == saved.tokens and loaded.prose == saved.prose
    assert loaded.uploaded_at.endswith("+00:00")


@pytest.mark.asyncio
async def test_save_rejects_before_writing_anything():
    s3 = FakeS3Store()
    with pytest.raises(DesignProfileError):
        await DesignProfileStore(s3).save(
            filename="bad.md", uploaded_by="admin@x",
            markdown="```tokens\nnope: #fff\n```\n")
    assert DESIGN_PROFILE_KEY not in s3.blobs


@pytest.mark.asyncio
async def test_corrupt_object_is_treated_as_absent():
    s3 = FakeS3Store()
    s3.blobs[DESIGN_PROFILE_KEY] = "{{{ not json"
    assert await DesignProfileStore(s3).load() is None


@pytest.mark.asyncio
async def test_load_returns_none_when_the_store_raises_a_non_missing_error():
    """FileNotFoundError(없음)만 fail-soft하던 시절의 회귀 가드다.
    AccessDenied·스로틀·네트워크 오류 같은 그 외 모든 예외도 None으로
    강등돼야 한다 -- 그러지 않으면 이 스토어를 쓰는
    PrototypeSession.start()가 통째로 죽는다(docstring이 약속한 것과
    정반대)."""
    class _BoomS3:
        async def get(self, key: str) -> str:
            raise RuntimeError("AccessDenied")

    assert await DesignProfileStore(_BoomS3()).load() is None


@pytest.mark.asyncio
async def test_remove_deletes_the_object():
    s3 = FakeS3Store()
    store = DesignProfileStore(s3)
    await store.save(filename="acme.md", uploaded_by="admin@x", markdown=GOOD_MD)
    await store.remove()
    assert DESIGN_PROFILE_KEY not in s3.blobs
    assert await store.load() is None


@pytest.mark.asyncio
async def test_no_bucket_is_readonly():
    store = DesignProfileStore(None)
    assert await store.load() is None
    with pytest.raises(DesignProfileError) as exc:
        await store.save(filename="a.md", uploaded_by="x", markdown=GOOD_MD)
    assert exc.value.code == "readonly"
