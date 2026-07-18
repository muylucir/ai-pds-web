# backend/tests/sandbox_contract.py
from __future__ import annotations
import pytest
from pathfinder.sandbox.base import Sandbox

async def _collect(aiter):
    return [e async for e in aiter]

async def assert_read_write_roundtrip(sb: Sandbox) -> None:
    await sb.write_file("aiplc-docs/audit.md", "hello")
    assert await sb.read_file("aiplc-docs/audit.md") == "hello"

async def assert_rejects_unsafe_paths(sb: Sandbox) -> None:
    for bad in ("../evil.md", "/etc/evil.md"):
        with pytest.raises(ValueError):
            await sb.write_file(bad, "x")
        with pytest.raises(ValueError):
            await sb.read_file(bad)
    with pytest.raises(ValueError):
        await sb.list_files("../*")

async def assert_list_glob_returns_relative_posix(sb: Sandbox) -> None:
    await sb.write_file("aiplc-docs/a-questions.md", "x")
    await sb.write_file("aiplc-docs/b-questions.md", "y")
    await sb.write_file("aiplc-docs/audit.md", "z")  # must not match the glob
    found = sorted(await sb.list_files("aiplc-docs/*-questions.md"))
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]

async def assert_send_message_ordered_and_terminates(sb: Sandbox) -> None:
    events = await _collect(sb.send_message("hello"))
    assert len(events) >= 1, "a turn must yield at least one event"
    assert events[-1].kind in ("done", "error"), "a turn must end with done/error"
    # exactly one terminal event, and it is last
    assert all(e.kind not in ("done", "error") for e in events[:-1])

async def assert_double_star_glob_matches_top_level_and_nested(sb: Sandbox) -> None:
    # C2: pathlib.Path.glob '**' semantics -- '**' matches ZERO or more path
    # segments, so 'subtree/**/*' must match BOTH a top-level file directly
    # under the subtree AND a nested file further down. A plain
    # fnmatch.fnmatch('**') implementation silently drops the top-level file.
    await sb.write_file("glob-subtree/top.md", "top")
    await sb.write_file("glob-subtree/nested/deep.md", "deep")
    found = sorted(await sb.list_files("glob-subtree/**/*"))
    assert found == ["glob-subtree/nested/deep.md", "glob-subtree/top.md"]

async def run_sandbox_contract(sb: Sandbox) -> None:
    await assert_read_write_roundtrip(sb)
    await assert_rejects_unsafe_paths(sb)
    await assert_list_glob_returns_relative_posix(sb)
    await assert_double_star_glob_matches_top_level_and_nested(sb)
    await assert_send_message_ordered_and_terminates(sb)
