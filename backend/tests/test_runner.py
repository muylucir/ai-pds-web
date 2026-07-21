import json
from pathlib import Path
import pytest
from pathfinder.runner import AgentRunner
from pathfinder.models import AgentEvent
from fakes.in_memory_s3 import FakeS3Store

Q_PAYLOAD = json.dumps({"interrupt_id": "i-7", "questions": {"name": "q", "questions": []}})
SESSION = {"session_id": "p1", "bucket": "", "region": "ap-northeast-1", "prefix": "sessions"}


class FakeDriver:
    """workspace(local_root) 파일을 실제로 만지는 최소 드라이버.
    run()은 files_written을 로컬 워크스페이스에 쓴 뒤 scripted 이벤트를 낸다."""
    def __init__(self, events=None, files_written=None, answers_events=None,
                 pending_payload=None, workspace=None):
        self._events = events or [AgentEvent(kind="message", text="ok"), AgentEvent(kind="done")]
        self._files = files_written or {}
        self._answers_events = answers_events
        self._pending = pending_payload
        self._workspace = workspace
        self.answer_calls = []

    async def _emit(self, evs):
        for k, v in self._files.items():
            (Path(self._workspace) / k).parent.mkdir(parents=True, exist_ok=True)
            (Path(self._workspace) / k).write_text(v, encoding="utf-8")
        for e in evs:
            yield e

    def run(self, text, session):
        return self._emit(self._events)

    def run_answers(self, interrupt_id, answers, session):
        self.answer_calls.append((interrupt_id, answers))
        return self._emit(self._answers_events or [AgentEvent(kind="done")])

    async def pending(self, session):
        return self._pending


def _runner(tmp_path, driver=None, s3=None):
    root = tmp_path / "ws"
    driver = driver or FakeDriver(workspace=root)
    if driver._workspace is None:
        driver._workspace = root
    return AgentRunner(project_id="p1", driver=driver, s3=s3 or FakeS3Store(),
                       local_root=root, session=SESSION)


async def _collect(aiter):
    return [e async for e in aiter]


async def test_file_ops_go_to_s3(tmp_path):
    r = _runner(tmp_path)
    await r.write_file("aiplc-docs/x.md", "hi")
    assert await r.read_file("aiplc-docs/x.md") == "hi"
    assert r._s3.blobs["aiplc-docs/x.md"] == "hi"


async def test_path_safety_rejected(tmp_path):
    r = _runner(tmp_path)
    with pytest.raises(ValueError):
        await r.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await r.list_files("../*")


async def test_list_files_double_star_glob(tmp_path):
    r = _runner(tmp_path)
    await r.write_file("aiplc-docs/top-questions.md", "t")
    await r.write_file("aiplc-docs/sub/nested-questions.md", "n")
    await r.write_file("aiplc-docs/audit.md", "a")
    found = sorted(await r.list_files("aiplc-docs/**/*-questions.md"))
    assert found == ["aiplc-docs/sub/nested-questions.md", "aiplc-docs/top-questions.md"]


async def test_send_message_relays_and_terminates(tmp_path):
    r = _runner(tmp_path)
    evs = await _collect(r.send_message("go"))
    assert evs[-1].kind == "done"


async def test_turn_syncs_written_files_to_s3(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(files_written={"aiplc-docs/aiplc-state.md": "stage: Discovery",
                                  "prototype/app.py": "print('hi')",
                                  "node_modules/pkg.js": "DROP"}, workspace=root)
    r = _runner(tmp_path, driver=d)
    await _collect(r.send_message("start"))
    assert r._s3.blobs["aiplc-docs/aiplc-state.md"] == "stage: Discovery"
    assert r._s3.blobs["prototype/app.py"] == "print('hi')"
    assert "node_modules/pkg.js" not in r._s3.blobs  # sync 글롭 밖


async def test_audit_md_redacted_at_rest(tmp_path):
    root = tmp_path / "ws"
    raw = "Setup.\nkey sk-abc123def456ghi789 used.\nEnd."
    d = FakeDriver(files_written={"aiplc-docs/audit.md": raw}, workspace=root)
    r = _runner(tmp_path, driver=d)
    await _collect(r.send_message("go"))
    synced = r._s3.blobs["aiplc-docs/audit.md"]
    assert "sk-abc123def456ghi789" not in synced
    assert "[CREDENTIAL REDACTED]" in synced


async def test_restore_pushes_s3_into_local_before_turn(tmp_path):
    root = tmp_path / "ws"
    s3 = FakeS3Store()
    s3.blobs["uploads/의견.md"] = "# 의견"
    d = FakeDriver(workspace=root)
    r = _runner(tmp_path, driver=d, s3=s3)
    await _collect(r.send_message("읽어줘"))
    assert (root / "uploads" / "의견.md").read_text(encoding="utf-8") == "# 의견"


async def test_sync_completes_before_done_yield(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(files_written={"aiplc-docs/aiplc-state.md": "stage: mid"}, workspace=root)
    r = _runner(tmp_path, driver=d)
    saw_done = False
    async for e in r.send_message("go"):
        if e.kind == "done":
            saw_done = True
            assert await r.read_file("aiplc-docs/aiplc-state.md") == "stage: mid"
    assert saw_done


async def test_concurrent_turn_busy_signal(tmp_path):
    r = _runner(tmp_path)
    r._turn_active = True
    evs = await _collect(r.send_message("second"))
    assert len(evs) == 1 and evs[0].kind == "error"
    assert "in progress" in evs[0].text


async def test_questions_event_arms_interrupt_and_answers_resume(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(events=[AgentEvent(kind="questions", payload=Q_PAYLOAD),
                          AgentEvent(kind="done")], workspace=root)
    r = _runner(tmp_path, driver=d)
    await _collect(r.send_message("시작"))
    await _collect(r.send_answers({"1": "A"}))
    assert d.answer_calls == [("i-7", {"1": "A"})]


async def test_send_answers_without_pending_errors(tmp_path):
    r = _runner(tmp_path)
    evs = await _collect(r.send_answers({"1": "A"}))
    assert evs[0].kind == "error" and "no pending questions" in evs[0].text


async def test_malformed_questions_payload_does_not_arm(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(events=[AgentEvent(kind="questions", payload="not-json{"),
                          AgentEvent(kind="done")], workspace=root)
    r = _runner(tmp_path, driver=d)
    evs = await _collect(r.send_message("시작"))
    assert evs[-1].kind == "done"
    assert r._pending_interrupt_id is None
    follow = await _collect(r.send_answers({"1": "A"}))
    assert follow[0].kind == "error"


async def test_pending_delegates_to_driver_and_arms(tmp_path):
    r = _runner(tmp_path, driver=FakeDriver(pending_payload=Q_PAYLOAD, workspace=tmp_path/"ws"))
    assert await r.pending() == Q_PAYLOAD
    assert r._pending_interrupt_id == "i-7"


async def test_pending_degrades_to_none_on_driver_error(tmp_path):
    class Raising(FakeDriver):
        async def pending(self, session):
            raise RuntimeError("dead")
    r = _runner(tmp_path, driver=Raising(workspace=tmp_path/"ws"))
    assert await r.pending() is None


async def test_stop_removes_local_root(tmp_path):
    r = _runner(tmp_path)
    await r.write_file("aiplc-docs/x.md", "hi")  # S3만 씀
    (r._local_root).mkdir(parents=True, exist_ok=True)
    (r._local_root / "scratch.txt").write_text("x", encoding="utf-8")
    await r.stop()
    assert not r._local_root.exists()


async def test_input_holder_settable(tmp_path):
    r = _runner(tmp_path)
    assert r.input_holder is None
    r.set_input_holder("facilitator-1")
    assert r.input_holder == "facilitator-1"
