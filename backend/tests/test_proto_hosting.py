# backend/tests/test_proto_hosting.py — 호스팅 절차와 기동 뒤 재호스팅
# (aipds/proto/hosting.py).
#
# 라우트와 재호스팅이 같은 절차여야 한다: 되살리기 → npm → 스냅샷 → 토큰 → 원하는 상태.
# 재시작 뒤 뜬 프로토타입이 같은 소스·같은 링크여야 한다는 것이 이 파일의 단정이다.
import asyncio

import pytest

import aipds.app as app_module
from aipds.proto.hosting import (adopt_local_state, host_prototype, rehost_desired,
                                 stop_prototype)
from aipds.proto.host import HostInfo
from aipds.proto.store import PrototypeStore
from fakes.in_memory_s3 import FakeS3Store

PID = "host-proj"


class FakeHost:
    """npm 수명 주기를 흉내내는 호스트. 동시에 몇 개가 올라가는지 잰다."""

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
    """S3에만 소스 세대가 있는 상태 — 교체된 인스턴스의 모양."""
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
    assert served.read_text() == '{"name":"a"}'       # S3에서 되살렸다
    store = env["store"]
    assert await store.load_token("a") == "tok-a"
    assert await store.desired_running() == ["a"]


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
    """한꺼번에 올리면 `npm install` + `next build`(피크 약 2GB)가 겹쳐 기동 직후
    OOM이 난다 — 하나씩 올린다."""
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
    """되살릴 소스가 없으면 원하는 상태를 남겨 둘 이유가 없다 — 매 기동마다 실패한다."""
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
    """원하는 상태를 기록하기 전에 뜬 호스팅도(이 저장소가 생기기 전, 기록 실패) 재시작
    뒤 다시 뜬다 — pid 파일이 "멈추라고 한 적 없이 끊긴 호스팅"을 말한다."""
    await _seed(env, "a")
    await adopt_local_state([(PID, "a")])
    assert await rehost_desired() == 1
    assert env["host"].started == [(PID, "a")]


async def test_a_local_build_without_a_generation_is_backfilled(env):
    """이 저장소가 생기기 전에 만든 프로토타입도 다음 교체를 견딘다."""
    build = env["proto_root"] / PID / "old"
    (build / "prototype").mkdir(parents=True)
    (build / "prototype" / "package.json").write_text("{}")
    await adopt_local_state([])
    assert dict(await env["store"].entries("old")) == {"prototype/package.json": b"{}"}
    # 원하는 상태는 만들지 않는다 — 호스팅되던 것이 아니다.
    assert await env["store"].desired_running() == []
