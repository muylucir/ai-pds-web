# backend/tests/test_workspace_sync.py
#
# 워크스페이스 파일 하나를 정본(S3)에 올리는 규칙의 단일 소유자.
#
# **왜 이 모듈이 생겼는가(2026-08-18).** 화면에 "문서가 작성되었다"고 나오는데 문서
# 탭 드롭다운에 없고, 목록에 보여서 골라도 내용이 없고, 잠깐 나타났다 사라지고,
# "문서 리뷰" 화면에서는 보였다. 증상 네 개가 한 원인이었다:
#
#     `file_changed`는 **로컬** 쓰기에서 즉시 알리는데, 정본(S3) 게시는 **턴 종료**
#     까지 미뤄졌다. UI의 읽기 경로는 전부 S3다(runner.read_file/list_files).
#
# 실측이 그대로 보였다: 한 프로젝트의 aiplc-docs 16개 파일의 S3 타임스탬프가 전부
# 같은 1초 안이었다 — 턴 중에는 아무것도 없고 끝에 몰아서 올라간다.
#
# 프론트는 이미 이 문제를 막으려 애쓰고 있었다(현재 path를 목록에 union, 404를
# "미동기화"로 구분). 그러나 읽을 것이 S3에 없으면 프론트가 할 수 있는 것이 없다.
#
# 계약은 질문 파일에 이미 적용한 것과 같다(a2b9623): **광고하기 전에 게시한다.**
#
# 규칙을 이 모듈이 소유하는 이유: 올리는 곳이 둘이 됐다(턴 종료 배치 sync와 쓰기
# 직후 게시). audit.md 리댁션과 대상 글롭이 두 곳에 복사되면 한쪽만 고쳐져
# **audit.md가 리댁션 없이 S3에 올라가는** 경로가 생긴다.
from __future__ import annotations

from pathlib import Path

import pytest

from pathfinder.workspace_sync import (SYNC_GLOBS, content_for_s3,
                                       is_synced_key, publish_file)
from tests.fakes.in_memory_s3 import FakeS3Store


def _ws(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.parametrize("key,expected", [
    ("aiplc-docs/audit.md", True),
    ("aiplc-docs/discovery/envision/pain-point-analysis.md", True),
    ("prototype/src/app/page.tsx", True),
    ("uploads/spec.pdf", True),
    ("CLAUDE.md", False),                    # 조립물 — 정본이 아니다
    (".proto-token", False),
    ("node_modules/x/index.js", False),
])
def test_only_the_synced_subtrees_are_published(key, expected):
    """턴 종료 배치 sync가 올리는 것과 **같은 집합**이어야 한다.

    다르면 쓰기 직후에 올라간 파일이 배치에서 빠지거나(그 반대) 하고, 그 어긋남은
    "있다가 없어지는 문서"로 보인다."""
    assert is_synced_key(key) is expected


def test_audit_is_redacted_on_the_way_to_s3():
    """audit.md는 S3를 직접 읽는 사람에게도 노출되므로 저장 시 리댁션한다.

    이 규칙이 배치 sync에만 있었다 — 쓰기 직후 게시가 그것을 빠뜨리면 audit.md가
    리댁션 없이 정본에 올라간다."""
    raw = "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE 로 접근했다"
    out = content_for_s3("aiplc-docs/audit.md", raw)
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_other_documents_are_not_rewritten():
    """산출물은 그대로 올린다 — 리댁션은 audit.md만이다(그 파일만 도구 출력을
    원문으로 담는다)."""
    raw = "# 분석\n\n본문 그대로."
    assert content_for_s3("aiplc-docs/discovery/x.md", raw) == raw


@pytest.mark.asyncio
async def test_publish_uploads_the_file(tmp_path):
    s3 = FakeS3Store()
    _ws(tmp_path, "aiplc-docs/discovery/x.md", "# 문서\n")
    assert await publish_file(s3, tmp_path, "aiplc-docs/discovery/x.md") is True
    assert await s3.get("aiplc-docs/discovery/x.md") == "# 문서\n"


@pytest.mark.asyncio
async def test_publish_applies_the_audit_rule(tmp_path):
    s3 = FakeS3Store()
    _ws(tmp_path, "aiplc-docs/audit.md", "key=AKIAIOSFODNN7EXAMPLE")
    await publish_file(s3, tmp_path, "aiplc-docs/audit.md")
    assert "AKIAIOSFODNN7EXAMPLE" not in await s3.get("aiplc-docs/audit.md")


@pytest.mark.asyncio
async def test_publish_skips_keys_outside_the_synced_subtrees(tmp_path):
    s3 = FakeS3Store()
    _ws(tmp_path, "CLAUDE.md", "조립물")
    assert await publish_file(s3, tmp_path, "CLAUDE.md") is False
    assert "CLAUDE.md" not in s3.blobs


@pytest.mark.asyncio
async def test_a_missing_local_file_is_not_an_error(tmp_path):
    """게시는 부수 동작이다 — 파일이 이미 지워졌다고 턴을 죽이면 안 된다."""
    s3 = FakeS3Store()
    assert await publish_file(s3, tmp_path, "aiplc-docs/gone.md") is False


@pytest.mark.asyncio
async def test_an_unsafe_key_is_refused(tmp_path):
    """경로 탈출은 fail-closed다 — 배치 sync가 `reject_unsafe`로 전체를 멈추는 것과
    같은 규율이고, 여기서는 그 한 파일을 거부한다."""
    s3 = FakeS3Store()
    assert await publish_file(s3, tmp_path, "aiplc-docs/../../etc/passwd") is False
    assert not s3.blobs.keys()


def test_the_glob_set_is_the_one_the_runner_uses():
    """배치 sync와 같은 상수를 쓰는지 — 두 벌로 두면 갈라진다."""
    from pathfinder.runner import AgentRunner
    assert AgentRunner._SYNC_GLOBS == SYNC_GLOBS


# ---- 계약: 광고하기 전에 게시한다 ----
# 보고된 증상은 **일반 문서**였다(질문 파일이 아니다). 훅이 `file_changed`를 흘리면
# UI가 곧바로 그 문서를 읽으러 오는데, 그 시점에 정본에 없으면 목록에도 없고 골라도
# 내용이 없다. 그래서 이벤트보다 게시가 먼저여야 한다.

async def _driver(tmp_path):
    from pathfinder.agent.claude_driver import ClaudeDriver
    ws = tmp_path / "ws"
    ws.mkdir()
    return ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "r"),
                        config_dir=str(tmp_path / "c"), s3=FakeS3Store(),
                        client_factory=lambda s: None), ws


@pytest.mark.asyncio
async def test_a_written_document_is_in_s3_when_file_changed_is_emitted(tmp_path):
    d, ws = await _driver(tmp_path)
    rel = "aiplc-docs/discovery/envision/pain-point-analysis.md"
    _ws(ws, rel, "# 페인 포인트 분석\n\n본문.\n")
    await d._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": str(ws / rel)}},
        "t", None)
    # 이벤트가 큐에 있다 = UI가 곧 읽으러 온다.
    assert any(e.kind == "file_changed" and e.path == rel for e in d._queue)
    # 그 순간 정본에 있어야 한다 — 턴 종료 sync를 기다리지 않는다.
    assert await d._s3.get(rel) == "# 페인 포인트 분석\n\n본문.\n"


@pytest.mark.asyncio
async def test_audit_is_redacted_when_published_from_the_hook(tmp_path):
    """배치 sync에만 있던 규칙이 이 경로에서 빠지면 audit.md가 리댁션 없이 올라간다."""
    d, ws = await _driver(tmp_path)
    rel = "aiplc-docs/audit.md"
    _ws(ws, rel, "key=AKIAIOSFODNN7EXAMPLE 로 접근")
    await d._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": str(ws / rel)}},
        "t", None)
    assert "AKIAIOSFODNN7EXAMPLE" not in await d._s3.get(rel)


@pytest.mark.asyncio
async def test_a_write_outside_the_workspace_publishes_nothing(tmp_path):
    d, ws = await _driver(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    await d._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": str(outside)}},
        "t", None)
    assert not d._s3.blobs.keys()
    assert any(e.kind == "status" for e in d._queue)
