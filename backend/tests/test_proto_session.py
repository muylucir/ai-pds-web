# backend/tests/test_proto_session.py
from __future__ import annotations
import asyncio
import json

import pytest

from pathfinder.globmatch import matches_glob
from pathfinder.models import AgentEvent
from pathfinder.proto.session import PrototypeSession
from pathfinder.proto.vm import BootSpec, FakeMicroVMController

from fakes.in_memory_s3 import FakeS3Store

SLUG = "todo-app"
PROJECT_ID = "proj-1"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
RULE_PUSH_PATH = "aiplc-rules/aws-aiplc-rule-details/discovery/prototype-building.md"


class FakeHarness:
    """In-memory HarnessClientLike test double: a dict file store + a
    scriptable event list for send_message. No HTTP."""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.answer_calls: list[tuple[str, dict]] = []
        self.interrupt_calls = 0
        self.send_answers_result = True
        self._message_script: list[AgentEvent] = []

    def script_messages(self, events: list[AgentEvent]) -> None:
        self._message_script = events

    async def send_message(self, text: str):
        for ev in self._message_script:
            yield ev

    async def send_answers(self, interrupt_id: str, answers: dict) -> bool:
        self.answer_calls.append((interrupt_id, answers))
        return self.send_answers_result

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    async def pending(self) -> str | None:
        return None

    async def read_file(self, rel_path: str) -> str:
        if rel_path not in self.files:
            raise FileNotFoundError(rel_path)
        return self.files[rel_path]

    async def write_file(self, rel_path: str, content: str) -> None:
        self.files[rel_path] = content

    async def list_files(self, glob: str) -> list[str]:
        return sorted(p for p in self.files if matches_glob(p, glob))

    async def heartbeat(self) -> bool:
        return True


def _make_rules_dir(tmp_path):
    rule_dir = tmp_path / "aiplc-rules" / "aws-aiplc-rule-details" / "discovery"
    rule_dir.mkdir(parents=True)
    (rule_dir / "prototype-building.md").write_text("RULE BODY", encoding="utf-8")
    return tmp_path / "aiplc-rules"


def _session(s3, rules_dir, harness, controller=None, idle_seconds=1800, token_minter=None):
    return PrototypeSession(
        project_id=PROJECT_ID,
        slug=SLUG,
        s3=s3,
        controller=controller or FakeMicroVMController(base_url="http://fake-harness"),
        spec=BootSpec(image_id="img", exec_role_arn="arn:aws:iam::123:role/x"),
        harness_factory=lambda base_url, headers: harness,
        rules_dir=rules_dir,
        idle_seconds=idle_seconds,
        token_minter=token_minter,
    )


# ---- start() ----

async def test_start_boots_pushes_spec_and_rule_and_becomes_ready(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec body"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    controller = FakeMicroVMController(base_url="http://fake-harness")
    session = _session(s3, rules_dir, harness, controller=controller)

    await session.start()

    assert controller.boot_calls == 1
    assert harness.files[SPEC_KEY] == "# spec body"
    assert harness.files[RULE_PUSH_PATH] == "RULE BODY"
    assert session.status == "ready"


async def test_start_raises_file_not_found_when_spec_missing(tmp_path):
    s3 = FakeS3Store()
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    controller = FakeMicroVMController(base_url="http://fake-harness")
    session = _session(s3, rules_dir, harness, controller=controller)

    with pytest.raises(FileNotFoundError):
        await session.start()

    assert controller.boot_calls == 0  # fails before ever booting a VM


async def test_start_restores_bundle_files_on_rebuild(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec body"
    s3.blobs[f"prototypes/{SLUG}/bundle/app.js"] = "console.log('hi')"
    s3.blobs[f"prototypes/{SLUG}/bundle/src/util.js"] = "export const x = 1;"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    session = _session(s3, rules_dir, harness)

    await session.start()

    assert harness.files["prototype/app.js"] == "console.log('hi')"
    assert harness.files["prototype/src/util.js"] == "export const x = 1;"
    assert session.status == "ready"


async def test_start_does_not_mint_token_for_fake_vm_ids(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    seen_headers = []

    def factory(base_url, headers):
        seen_headers.append(headers)
        return harness

    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=s3,
        controller=FakeMicroVMController(base_url="http://fake-harness"),
        spec=BootSpec(image_id="img", exec_role_arn="arn:aws:iam::123:role/x"),
        harness_factory=factory, rules_dir=rules_dir,
    )
    await session.start()
    assert seen_headers == [{}]


async def test_start_mints_token_via_provided_minter(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    seen = []
    minted_vm_ids = []

    def minter(vm_id: str) -> dict:
        minted_vm_ids.append(vm_id)
        return {"X-aws-proxy-auth": "jwe-abc"}

    def factory(base_url, headers):
        seen.append(headers)
        return harness

    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=s3,
        controller=FakeMicroVMController(base_url="http://fake-harness"),
        spec=BootSpec(image_id="img", exec_role_arn="arn:aws:iam::123:role/x"),
        harness_factory=factory, rules_dir=rules_dir, token_minter=minter,
    )
    await session.start()
    assert seen == [{"X-aws-proxy-auth": "jwe-abc"}]
    assert minted_vm_ids == ["fake-proj-1-1"]


# ---- send_message: relay + status transitions ----

async def test_send_message_relays_events_and_returns_to_ready(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    session = _session(s3, rules_dir, harness)
    await session.start()

    harness.script_messages([
        AgentEvent(kind="message", text="building..."),
        AgentEvent(kind="done"),
    ])
    seen = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in seen] == ["message", "done"]
    assert session.status == "ready"


async def test_send_message_sets_waiting_input_on_questions_event(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    session = _session(s3, rules_dir, harness)
    await session.start()

    payload = json.dumps({"interrupt_id": "iid-1", "questions": {"name": "q"}})
    harness.script_messages([AgentEvent(kind="questions", payload=payload)])
    seen = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in seen] == ["questions"]
    assert session.status == "waiting_input"


# ---- send_answers: session-owned interrupt_id ----

async def test_send_answers_consumes_pending_interrupt_id(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    session = _session(s3, rules_dir, harness)
    await session.start()

    payload = json.dumps({"interrupt_id": "iid-1", "questions": {"name": "q"}})
    harness.script_messages([AgentEvent(kind="questions", payload=payload)])
    [ev async for ev in session.send_message("go")]

    ok = await session.send_answers({"1": "A"})

    assert ok is True
    assert harness.answer_calls == [("iid-1", {"1": "A"})]
    assert session.status == "building"
    # consumed: a second call with nothing new pending must fail
    ok2 = await session.send_answers({"1": "B"})
    assert ok2 is False


async def test_send_answers_false_when_nothing_pending(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    session = _session(s3, rules_dir, harness)
    await session.start()

    ok = await session.send_answers({"1": "A"})
    assert ok is False
    assert harness.answer_calls == []  # harness never touched


# ---- close(): S3 bundle sync + exclusions + VM stop ----

async def test_close_syncs_bundle_excludes_build_artifacts_and_stops_vm(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    controller = FakeMicroVMController(base_url="http://fake-harness")
    session = _session(s3, rules_dir, harness, controller=controller)
    await session.start()

    harness.files.update({
        "prototype/app.js": "console.log(1)",
        "prototype/README.md": "# howto",
        "prototype/node_modules/pkg/index.js": "module.exports = {}",
        "prototype/.next/cache/x.bin": "binary",
        "prototype/.git/HEAD": "ref: refs/heads/main",
        "prototype/src/nested/deep.js": "export default 1;",
    })

    await session.close()

    bundle = f"prototypes/{SLUG}/bundle/"
    assert s3.blobs[f"{bundle}app.js"] == "console.log(1)"
    assert s3.blobs[f"{bundle}README.md"] == "# howto"
    assert s3.blobs[f"{bundle}src/nested/deep.js"] == "export default 1;"
    assert f"{bundle}node_modules/pkg/index.js" not in s3.blobs
    assert f"{bundle}.next/cache/x.bin" not in s3.blobs
    assert f"{bundle}.git/HEAD" not in s3.blobs
    assert controller.stop_calls == 1
    assert session.status == "closed"


async def test_close_still_stops_vm_when_sync_fails(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    controller = FakeMicroVMController(base_url="http://fake-harness")
    session = _session(s3, rules_dir, harness, controller=controller)
    await session.start()

    async def _boom(glob):
        raise RuntimeError("harness unreachable")
    harness.list_files = _boom  # type: ignore[assignment]

    await session.close()

    assert controller.stop_calls == 1  # no VM leak despite sync failure
    assert session.status == "failed"


async def test_close_is_idempotent(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    controller = FakeMicroVMController(base_url="http://fake-harness")
    session = _session(s3, rules_dir, harness, controller=controller)
    await session.start()

    await session.close()
    await session.close()

    assert controller.stop_calls == 1
    assert session.status == "closed"


# ---- idle timer: auto-close ----

async def test_idle_timer_auto_closes_session(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    controller = FakeMicroVMController(base_url="http://fake-harness")
    session = _session(s3, rules_dir, harness, controller=controller, idle_seconds=0.05)

    await session.start()
    assert session.status == "ready"

    await asyncio.sleep(0.2)

    assert session.status == "closed"
    assert controller.stop_calls == 1


async def test_idle_timer_resets_on_send_message(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    controller = FakeMicroVMController(base_url="http://fake-harness")
    session = _session(s3, rules_dir, harness, controller=controller, idle_seconds=0.1)
    await session.start()

    await asyncio.sleep(0.06)
    harness.script_messages([AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]  # re-arms the timer

    await asyncio.sleep(0.06)
    assert session.status == "ready"  # would be "closed" if the timer hadn't reset

    await asyncio.sleep(0.1)
    assert session.status == "closed"
    assert controller.stop_calls == 1


# ---- first_prompt(): five directives ----

def test_first_prompt_covers_five_directives(tmp_path):
    s3 = FakeS3Store()
    rules_dir = _make_rules_dir(tmp_path)
    harness = FakeHarness()
    session = _session(s3, rules_dir, harness)

    prompt = session.first_prompt()

    assert SPEC_KEY in prompt
    assert "AskUserQuestion" in prompt
    assert "/workspace/prototype/" in prompt
    assert "README" in prompt
    assert f"/api/proto/{PROJECT_ID}/{SLUG}/" in prompt
    assert "basePath" in prompt or "상대 경로" in prompt
    assert "Bedrock" in prompt
    assert "하드코딩" in prompt


async def test_start_stops_vm_when_post_boot_push_fails(tmp_path):
    """boot() succeeded but a later push step raised: the VM must be stopped
    before the exception propagates — the route drops the session object on
    a start() failure, so an unstopped VM would leak unreferenced (billing)
    until the next restart's orphan sweep. Final-review finding I1."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    controller = FakeMicroVMController(base_url="http://fake")

    class BoomHarness:
        async def write_file(self, path, content):
            raise RuntimeError("push failed")

    rules_dir = _make_rules_dir(tmp_path)

    session = PrototypeSession(
        PROJECT_ID, SLUG, s3, controller, BootSpec(image_id="fake-img"),
        lambda base_url, headers: BoomHarness(), rules_dir=rules_dir)

    with pytest.raises(RuntimeError):
        await session.start()

    assert controller.boot_calls == 1
    assert controller.stop_calls == 1  # VM not leaked
    assert session.status == "failed"


async def test_close_survives_vm_stop_failure(tmp_path):
    """controller.stop() raising must not propagate out of close() — the
    route's registry cleanup runs after close() returns; a raise would wedge
    the session (409 on every future start). Final-review finding M1."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    class StopBoom(FakeMicroVMController):
        async def stop(self, handle):
            raise RuntimeError("stop failed")

    controller = StopBoom(base_url="http://fake")
    rules_dir = _make_rules_dir(tmp_path)

    harness = FakeHarness()
    session = PrototypeSession(
        PROJECT_ID, SLUG, s3, controller, BootSpec(image_id="fake-img"),
        lambda base_url, headers: harness, rules_dir=rules_dir)
    await session.start()

    await session.close()  # must NOT raise
    assert session.status == "failed"
    # Idempotency preserved even after a failed stop.
    await session.close()
