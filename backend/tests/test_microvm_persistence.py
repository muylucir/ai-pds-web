import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store
from pathfinder.sandbox.base import AgentEvent

def _sandbox():
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_write_then_read_uses_s3_without_booting():
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/audit.md", "entry")
    assert await sb.read_file("aiplc-docs/audit.md") == "entry"
    assert ctrl.boot_calls == 0            # true laziness: NO VM for file ops
    assert s3.blobs["aiplc-docs/audit.md"] == "entry"   # landed in durable S3
    assert harness.files == {}             # harness NOT touched by file ops

async def test_list_files_globs_over_s3_without_booting():
    sb, ctrl, _, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/a-questions.md", "x")
    await sb.write_file("aiplc-docs/b-questions.md", "y")
    await sb.write_file("aiplc-docs/audit.md", "z")   # must not match
    found = sorted(await sb.list_files("aiplc-docs/*-questions.md"))
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]
    assert ctrl.boot_calls == 0

async def test_read_missing_from_s3_raises_filenotfound():
    sb, _, _, _ = _sandbox()
    await sb.start()
    with pytest.raises(FileNotFoundError):
        await sb.read_file("aiplc-docs/missing.md")

async def test_path_safety_runs_before_s3():
    sb, ctrl, _, s3 = _sandbox()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.read_file("/etc/passwd")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert s3.blobs == {}                  # nothing written past the guard
    assert ctrl.boot_calls == 0

def _sandbox_with_agent_writes(files_written: dict[str, str]):
    """A FakeHarness whose 'turn' writes files into the VM FS (like Claude Code
    does), so we can assert the post-turn sync pulls them into S3."""
    harness = FakeHarness()

    async def _turn(text: str):
        for k, v in files_written.items():
            harness.files[k] = v            # agent writes to the VM FS
        yield AgentEvent(kind="message", text="worked")
        yield AgentEvent(kind="done")

    harness._events_for = None
    harness.send_message = _turn            # override the canned echo turn
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1", controller=ctrl, spec=BootSpec(),
        harness_factory=lambda handle: harness, s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_turn_syncs_agent_written_files_to_s3():
    sb, _, _, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/aiplc-state.md": "stage: Discovery",
        "aiplc-docs/audit.md": "entry 1",
        "prototype/app.py": "print('hi')",
    })
    await sb.start()
    _ = [e async for e in sb.send_message("start ai-plc")]
    # After the turn, S3 (durable) holds what the agent wrote in the VM.
    assert s3.blobs["aiplc-docs/aiplc-state.md"] == "stage: Discovery"
    assert s3.blobs["aiplc-docs/audit.md"] == "entry 1"
    assert s3.blobs["prototype/app.py"] == "print('hi')"

async def test_route_read_after_turn_sees_synced_state():
    sb, _, _, _ = _sandbox_with_agent_writes({"aiplc-docs/aiplc-state.md": "stage: Envision"})
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    # read_file goes to S3 (Task 3); it must reflect the just-synced turn output.
    assert await sb.read_file("aiplc-docs/aiplc-state.md") == "stage: Envision"

async def test_only_sync_subtrees_are_pushed():
    sb, _, harness, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/audit.md": "keep",
        "node_modules/pkg/index.js": "DROP",   # outside the sync globs
    })
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    assert "aiplc-docs/audit.md" in s3.blobs
    assert "node_modules/pkg/index.js" not in s3.blobs

async def test_audit_md_is_redacted_at_rest_on_sync():
    # security decision: redact-on-sync -- audit.md's raw content must never
    # land in durable S3, even though every app-side read path already
    # redacts (parsers/audit.py, routes/turns.py). This closes the exposure
    # to a direct S3 reader.
    raw = "Setup notes.\nkey sk-abc123def456ghi789 was used.\nEnd of entry."
    sb, _, _, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/audit.md": raw,
    })
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    synced = await sb.read_file("aiplc-docs/audit.md")
    assert "sk-abc123def456ghi789" not in synced
    assert "[CREDENTIAL REDACTED]" in synced
    assert "Setup notes." in synced and "End of entry." in synced

async def test_only_audit_md_is_redacted_other_docs_stay_raw():
    # Locks the only-audit scope: a non-audit doc with the same
    # credential-shaped string is synced RAW (unchanged).
    raw = "Discovery notes.\nkey sk-abc123def456ghi789 was used."
    sb, _, _, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/discovery.md": raw,
    })
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    assert s3.blobs["aiplc-docs/discovery.md"] == raw

async def test_list_files_double_star_glob_matches_top_level_questions_file():
    # C2: production list_files must match pathlib.Path.glob '**' semantics
    # (zero-or-more segments), not plain fnmatch.fnmatch. list_question_files
    # uses exactly this glob shape ("aiplc-docs/**/*-questions.md") and a
    # plain-fnmatch implementation silently drops a top-level questions file.
    sb, ctrl, _, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/top-questions.md", "top")
    await sb.write_file("aiplc-docs/sub/nested-questions.md", "nested")
    found = sorted(await sb.list_files("aiplc-docs/**/*-questions.md"))
    assert found == ["aiplc-docs/sub/nested-questions.md", "aiplc-docs/top-questions.md"]
    assert ctrl.boot_calls == 0

async def test_sync_completes_before_terminal_event_is_yielded():
    # I1: send_message's post-turn S3 sync must complete BEFORE the terminal
    # ("done"/"error") event is yielded to the caller, not after. A route/SSE
    # client reacting to `done` (e.g. re-reading a file) must never race the
    # sync and observe pre-sync (stale) S3 state.
    sb, _, harness, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/aiplc-state.md": "stage: mid-turn",
    })
    await sb.start()
    saw_done = False
    async for event in sb.send_message("go"):
        if event.kind == "done":
            saw_done = True
            # At the moment `done` is observed, S3 must already reflect the
            # turn's writes -- i.e. sync ran BEFORE this yield, not after.
            assert await sb.read_file("aiplc-docs/aiplc-state.md") == "stage: mid-turn"
    assert saw_done
