# backend/tests/test_proto_hosting.py — the hosting procedure and re-hosting after startup
# (aipds/proto/hosting.py).
#
# The route and re-hosting must follow the same procedure: restore → npm → snapshot → token → desired state.
# This file asserts that a prototype brought up after a restart has the same source and the same link.
import asyncio

import pytest

import aipds.app as app_module
from aipds.proto.hosting import (adopt_local_state, host_prototype, rehost_desired,
                                 stop_prototype)
from aipds.proto.host import HostInfo
from aipds.proto.store import PrototypeStore
from fakes.in_memory_s3 import FakeS3Store

PID = "host-proj"
#: The value of a link already handed out (the S3 source of truth). The name does not include token — bandit B105 looks at names.
HANDED_OUT = "tok-handed-out"


class FakeHost:
    """A host that mimics the npm lifecycle. Measures how many come up at the same time."""

    def __init__(self, root=None):
        self.root = root
        self.started: list[tuple[str, str]] = []
        self.tokens: dict[tuple[str, str], str] = {}
        self.infos: dict[tuple[str, str], HostInfo] = {}
        self.active = 0
        self.max_active = 0
        self.fail: set[str] = set()

    async def start(self, pid, slug, cwd=None, base_path=None, model_id=None):
        if not cwd.is_dir():
            raise FileNotFoundError(str(cwd))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        self.started.append((pid, slug))
        state = "failed" if slug in self.fail else "running"
        info = HostInfo(state=state, port=4000 + len(self.started), log_tail="log")
        self.infos[(pid, slug)] = info
        return info

    async def stop(self, pid, slug):
        self.infos.pop((pid, slug), None)

    def ensure_token(self, pid, slug):
        return self.tokens.setdefault((pid, slug), f"tok-{slug}")

    def token_for(self, pid, slug):
        return self.tokens.get((pid, slug))

    def adopt_token(self, token, pid, slug):
        self.tokens[(pid, slug)] = token

    def status(self, pid, slug):
        return self.infos.get((pid, slug))

    def slugs(self, pid):
        base = self.root / pid
        return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


class NoProfile:
    async def load(self):
        return None


@pytest.fixture
def env(monkeypatch, tmp_path):
    s3, root = FakeS3Store(), FakeS3Store()
    host = FakeHost(root=tmp_path)
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bucket")
    monkeypatch.setattr(app_module, "proto_host", lambda: host)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: root)
    monkeypatch.setattr(app_module, "design_profile_store", lambda: NoProfile())
    app_module.registry.register(PID, "p")
    yield {"s3": s3, "root": root, "host": host, "proto_root": tmp_path,
           "store": PrototypeStore(s3, root=root, project_id=PID)}
    app_module.registry.remove(PID)


async def _seed(env, slug, files=None):
    """A state where the source generation exists only in S3 — the shape of a replaced instance."""
    build = env["proto_root"] / "_seed" / slug
    for rel, body in (files or {"prototype/package.json": "{}"}).items():
        (build / rel).parent.mkdir(parents=True, exist_ok=True)
        (build / rel).write_text(body)
    await env["store"].snapshot(slug, build, "seed")


async def test_hosting_restores_the_tree_then_records_token_and_intent(env):
    await _seed(env, "a", {"prototype/package.json": '{"name":"a"}'})
    info, token = await host_prototype(PID, "a")

    assert info.state == "running" and token == "tok-a"
    served = env["proto_root"] / PID / "a" / "prototype" / "package.json"
    assert served.read_text() == '{"name":"a"}'       # restored from S3
    store = env["store"]
    assert await store.load_token("a") == "tok-a"
    assert await store.desired_running() == ["a"]


async def test_a_replaced_instance_keeps_the_token_it_already_handed_out(env):
    """Hosting on an instance with no local token (right after replacement) plants S3's token — minting
    a new one would kill the links already handed out."""
    await _seed(env, "a")
    await env["store"].save_token("a", HANDED_OUT)
    info, token = await host_prototype(PID, "a")

    assert info.state == "running" and token == HANDED_OUT
    assert env["host"].token_for(PID, "a") == HANDED_OUT


async def test_a_failed_host_records_neither_token_nor_intent(env):
    await _seed(env, "a")
    env["host"].fail.add("a")
    info, token = await host_prototype(PID, "a")
    assert info.state == "failed" and token is None
    assert await env["store"].load_token("a") is None
    assert await env["store"].desired_running() == []


async def test_stopping_forgets_the_intent(env):
    await _seed(env, "a")
    await host_prototype(PID, "a")
    await stop_prototype(PID, "a")
    assert await env["store"].desired_running() == []


async def test_startup_rehosts_only_what_should_run_one_at_a_time(env):
    """Bringing them all up at once overlaps `npm install` + `next build` (peak about 2GB) and causes an
    OOM right after startup — they come up one at a time."""
    for slug in ("a", "b", "c"):
        await _seed(env, slug)
    await env["store"].set_desired_running("a")
    await env["store"].set_desired_running("c")

    assert await rehost_desired() == 2
    assert env["host"].started == [(PID, "a"), (PID, "c")]
    assert env["host"].max_active == 1


async def test_startup_skips_what_is_already_running(env):
    await _seed(env, "a")
    await host_prototype(PID, "a")
    env["host"].started.clear()
    assert await rehost_desired() == 0
    assert env["host"].started == []


async def test_an_intent_with_no_source_left_is_dropped(env):
    """With no source to restore, there is no reason to keep the desired state — it would fail on every startup."""
    await env["store"].set_desired_running("ghost")
    assert await rehost_desired() == 0
    assert await env["store"].desired_running() == []


async def test_one_failure_does_not_block_the_rest(env):
    for slug in ("a", "b"):
        await _seed(env, slug)
        await env["store"].set_desired_running(slug)
    env["host"].fail.add("a")
    assert await rehost_desired() == 1
    assert env["host"].started == [(PID, "a"), (PID, "b")]



async def test_what_was_hosted_before_the_restart_comes_back(env):
    """Hosting that came up before the desired state was recorded (before this store existed, or a failed record) also
    comes back up after a restart — the pid file says "hosting that was cut off without ever being told to stop"."""
    await _seed(env, "a")
    await adopt_local_state([(PID, "a")])
    assert await rehost_desired() == 1
    assert env["host"].started == [(PID, "a")]


async def test_a_local_build_without_a_generation_is_backfilled(env):
    """Prototypes created before this store existed also survive the next replacement."""
    build = env["proto_root"] / PID / "old"
    (build / "prototype").mkdir(parents=True)
    (build / "prototype" / "package.json").write_text("{}")
    await adopt_local_state([])
    assert dict(await env["store"].entries("old")) == {"prototype/package.json": b"{}"}
    # No desired state is created — it was not being hosted.
    assert await env["store"].desired_running() == []
