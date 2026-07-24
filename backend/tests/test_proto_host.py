# backend/tests/test_proto_host.py
from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from pathfinder.proto.host import ProtoHost

from fakes.in_memory_s3 import FakeS3Store

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "proto_npm_stub"
SLUG = "todo-app"
PID = "proj-1"


async def _seed_bundle(s3: FakeS3Store, slug: str = SLUG, fixture_dir: Path = FIXTURE_DIR) -> None:
    prefix = f"prototypes/{slug}/bundle/"
    for path in fixture_dir.iterdir():
        if path.is_file():
            await s3.put(f"{prefix}{path.name}", path.read_text(encoding="utf-8"))


@pytest.fixture
def root(tmp_path):
    return tmp_path / "proto-host-root"


async def test_start_reaches_running_and_serves_http(root):
    s3 = FakeS3Store()
    await _seed_bundle(s3)
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG)

    try:
        assert info.state == "running"
        assert info.port is not None

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{info.port}/")
        assert resp.text == "stub ok"

        status = host.status(PID, SLUG)
        assert status is not None
        assert status.state == "running"
        assert status.port == info.port
    finally:
        await host.stop(PID, SLUG)


async def test_port_scan_skips_occupied_port(root):
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 4001))
    occupied.listen(1)
    try:
        s3 = FakeS3Store()
        await _seed_bundle(s3)
        host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

        info = await host.start(PID, SLUG)
        try:
            assert info.state == "running"
            assert info.port != 4001
            assert info.port in range(4001, 4010)
        finally:
            await host.stop(PID, SLUG)
    finally:
        occupied.close()


async def test_stop_terminates_process(root):
    s3 = FakeS3Store()
    await _seed_bundle(s3)
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG)
    assert info.state == "running"

    await host.stop(PID, SLUG)

    status = host.status(PID, SLUG)
    assert status is not None
    assert status.state == "stopped"

    # The port should be free again (process actually died).
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", info.port))
    finally:
        probe.close()


async def test_stop_is_idempotent_for_unknown_slug(root):
    s3 = FakeS3Store()
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))
    await host.stop(PID, "never-started")  # must not raise


async def test_broken_package_json_fails_with_npm_error_in_log(root, tmp_path):
    broken_dir = tmp_path / "broken_fixture"
    broken_dir.mkdir()
    (broken_dir / "package.json").write_text(
        '{"name": "broken", scripts: {"start": "node server.js"', encoding="utf-8"
    )

    s3 = FakeS3Store()
    await _seed_bundle(s3, slug="broken-app", fixture_dir=broken_dir)
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

    info = await host.start(PID, "broken-app")

    assert info.state == "failed"
    assert "npm" in info.log_tail.lower() or "json" in info.log_tail.lower()


async def test_status_of_unknown_slug_returns_none(root):
    s3 = FakeS3Store()
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))
    assert host.status(PID, "never-heard-of-it") is None


async def test_start_raises_file_not_found_for_empty_bundle(root):
    s3 = FakeS3Store()  # nothing seeded under prototypes/{slug}/bundle/
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

    with pytest.raises(FileNotFoundError):
        await host.start(PID, "no-such-slug")

    assert host.status(PID, "no-such-slug") is None


async def test_unsafe_bundle_keys_are_skipped(root):
    s3 = FakeS3Store()
    prefix = f"prototypes/{SLUG}/bundle/"
    # Legit files from the fixture...
    await _seed_bundle(s3)
    # ...plus unsafe keys that must be skipped rather than escaping target_dir.
    await s3.put(f"{prefix}../escape.txt", "should not land outside target dir")
    await s3.put(f"{prefix}nested/../../escape2.txt", "should not land outside target dir")

    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))
    info = await host.start(PID, SLUG)
    try:
        assert info.state == "running"
        assert not (root / "escape.txt").exists()
        assert not (root / "escape2.txt").exists()
        assert not (root.parent / "escape.txt").exists()
    finally:
        await host.stop(PID, SLUG)


async def test_build_script_failure_marks_failed(root, tmp_path):
    build_fail_dir = tmp_path / "build_fail_fixture"
    build_fail_dir.mkdir()
    (build_fail_dir / "package.json").write_text(
        '{"name": "buildfail", "scripts": {"build": "exit 1", "start": "node server.js"}}',
        encoding="utf-8",
    )
    (build_fail_dir / "server.js").write_text(
        (FIXTURE_DIR / "server.js").read_text(encoding="utf-8"), encoding="utf-8"
    )

    s3 = FakeS3Store()
    await _seed_bundle(s3, slug="build-fail-app", fixture_dir=build_fail_dir)
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

    info = await host.start(PID, "build-fail-app")

    assert info.state == "failed"
    assert info.port is None  # never reached the port-scan/start phase


async def test_no_start_script_falls_back_to_dev(root, tmp_path):
    dev_dir = tmp_path / "dev_fixture"
    dev_dir.mkdir()
    (dev_dir / "package.json").write_text(
        '{"name": "devonly", "scripts": {"dev": "node server.js"}}', encoding="utf-8"
    )
    (dev_dir / "server.js").write_text(
        (FIXTURE_DIR / "server.js").read_text(encoding="utf-8"), encoding="utf-8"
    )

    s3 = FakeS3Store()
    await _seed_bundle(s3, slug="dev-app", fixture_dir=dev_dir)
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

    info = await host.start(PID, "dev-app")
    try:
        assert info.state == "running"
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{info.port}/")
        assert resp.text == "stub ok"
    finally:
        await host.stop(PID, "dev-app")


async def test_log_tail_returns_last_n_lines(root):
    s3 = FakeS3Store()
    await _seed_bundle(s3)
    host = ProtoHost(s3=s3, root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG)
    try:
        assert info.state == "running"
        tail = host.log_tail(PID, SLUG, lines=5)
        assert isinstance(tail, str)
        assert len(tail.splitlines()) <= 5
    finally:
        await host.stop(PID, SLUG)
