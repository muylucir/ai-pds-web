# backend/tests/test_workspace_sync.py
#
# Single owner of the rules for publishing one workspace file to the source of truth (S3).
#
# **Why this module exists (2026-08-18).** The screen said "the document was written", yet
# it was missing from the document tab dropdown; when it did appear in the list, selecting
# it showed no content; it appeared briefly and vanished; and it was visible on the
# "document review" screen. The four symptoms had one cause:
#
#     `file_changed` announces a **local** write immediately, but publishing to the source
#     of truth (S3) was deferred until **turn end**. Every UI read path is S3
#     (runner.read_file/list_files).
#
# The measurements showed exactly that: the S3 timestamps of all 16 aiplc-docs files in one
# project fell within the same second. Nothing goes up during the turn; it all goes up at the end.
#
# The frontend was already trying hard to paper over this (unioning the current path into
# the list, distinguishing 404 as "not yet synced"). But if there is nothing in S3 to read,
# the frontend can do nothing.
#
# The contract is the same one already applied to question files (a2b9623): **publish
# before announcing.**
#
# Why this module owns the rules: there are now two places that upload (the turn-end batch
# sync and publish-right-after-write). If the audit.md redaction and the target globs were
# copied into both, one side would get fixed and not the other, creating a path where
# **audit.md goes up to S3 unredacted**.
from __future__ import annotations

from pathlib import Path

import pytest

from aipds.workspace_sync import (SYNC_GLOBS, content_for_s3,
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
    # Inputs the upload route writes directly to the source of truth. Uploading them as text
    # would overwrite the original (image, PDF) with a corrupted copy.
    ("uploads/spec.pdf", False),
    ("CLAUDE.md", False),                    # assembled file, not a source of truth
    (".proto-token", False),
    ("node_modules/x/index.js", False),
])
def test_only_the_synced_subtrees_are_published(key, expected):
    """Must be **the same set** that the turn-end batch sync uploads.

    Otherwise a file uploaded right after a write is missing from the batch (or vice
    versa), and that mismatch shows up as "a document that is there and then gone"."""
    assert is_synced_key(key) is expected


def test_audit_is_redacted_on_the_way_to_s3():
    """audit.md is also exposed to anyone reading S3 directly, so it is redacted on save.

    This rule used to live only in the batch sync; if publish-right-after-write misses it,
    audit.md goes up to the source of truth unredacted."""
    raw = "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE 로 접근했다"
    out = content_for_s3("aiplc-docs/audit.md", raw)
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_other_documents_are_not_rewritten():
    """Artifacts are uploaded as is; only audit.md is redacted (it is the only file that
    holds raw tool output)."""
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
    """Publishing is a side effect; the turn must not die because the file was already deleted."""
    s3 = FakeS3Store()
    assert await publish_file(s3, tmp_path, "aiplc-docs/gone.md") is False


@pytest.mark.asyncio
async def test_an_unsafe_key_is_refused(tmp_path):
    """Path escapes fail closed: the same discipline as the batch sync halting everything via
    `reject_unsafe`; here, that one file is rejected."""
    s3 = FakeS3Store()
    assert await publish_file(s3, tmp_path, "aiplc-docs/../../etc/passwd") is False
    assert not s3.blobs.keys()


def test_the_glob_set_is_the_one_the_runner_uses():
    """Whether it uses the same constant as the batch sync; two copies would drift apart."""
    from aipds.runner import AgentRunner
    assert AgentRunner._SYNC_GLOBS == SYNC_GLOBS


# ---- Contract: publish before announcing ----
# The reported symptom was with **ordinary documents** (not question files). When the hook
# emits `file_changed`, the UI immediately comes to read that document; if it is not in the
# source of truth at that point, it is neither in the list nor has content when selected.
# So publishing must come before the event.

async def _driver(tmp_path):
    from aipds.agent.claude_driver import ClaudeDriver
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
    # The event is in the queue = the UI will come to read it soon.
    assert any(e.kind == "file_changed" and e.path == rel for e in d._queue)
    # It must be in the source of truth at that moment, without waiting for the turn-end sync.
    assert await d._s3.get(rel) == "# 페인 포인트 분석\n\n본문.\n"


@pytest.mark.asyncio
async def test_audit_is_redacted_when_published_from_the_hook(tmp_path):
    """If this path missed the rule that used to live only in the batch sync, audit.md would go up unredacted."""
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
