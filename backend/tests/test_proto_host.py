# backend/tests/test_proto_host.py
from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from pathfinder.proto.host import ProtoHost

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "proto_npm_stub"
SLUG = "todo-app"
PID = "proj-1"


def _seed_build_dir(root: Path, pid: str = PID, slug: str = SLUG,
                    fixture_dir: Path = FIXTURE_DIR) -> Path:
    target = root / pid / slug
    target.mkdir(parents=True, exist_ok=True)
    for path in fixture_dir.iterdir():
        if path.is_file():
            (target / path.name).write_text(path.read_text(encoding="utf-8"),
                                            encoding="utf-8")
    return target


@pytest.fixture
def root(tmp_path):
    return tmp_path / "proto-host-root"


async def test_start_reaches_running_and_serves_http(root):
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

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
        _seed_build_dir(root)
        host = ProtoHost(root=root, port_range=range(4001, 4010))

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
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

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
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    await host.stop(PID, "never-started")  # must not raise


async def test_broken_package_json_fails_with_npm_error_in_log(root, tmp_path):
    broken_dir = tmp_path / "broken_fixture"
    broken_dir.mkdir()
    (broken_dir / "package.json").write_text(
        '{"name": "broken", scripts: {"start": "node server.js"', encoding="utf-8"
    )

    _seed_build_dir(root, slug="broken-app", fixture_dir=broken_dir)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    info = await host.start(PID, "broken-app")

    assert info.state == "failed"
    assert "npm" in info.log_tail.lower() or "json" in info.log_tail.lower()


async def test_status_of_unknown_slug_returns_none(root):
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    assert host.status(PID, "never-heard-of-it") is None


async def test_start_raises_file_not_found_for_empty_bundle(root):
    host = ProtoHost(root=root, port_range=range(4001, 4010))  # nothing seeded

    with pytest.raises(FileNotFoundError):
        await host.start(PID, "no-such-slug")

    assert host.status(PID, "no-such-slug") is None


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

    _seed_build_dir(root, slug="build-fail-app", fixture_dir=build_fail_dir)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

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

    _seed_build_dir(root, slug="dev-app", fixture_dir=dev_dir)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    info = await host.start(PID, "dev-app")
    try:
        assert info.state == "running"
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{info.port}/")
        assert resp.text == "stub ok"
    finally:
        await host.stop(PID, "dev-app")


async def test_log_tail_returns_last_n_lines(root):
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG)
    try:
        assert info.state == "running"
        tail = host.log_tail(PID, SLUG, lines=5)
        assert isinstance(tail, str)
        assert len(tail.splitlines()) <= 5
    finally:
        await host.stop(PID, SLUG)


# ---- in-place hosting (post-MicroVM): the build directory IS the served tree ----

async def test_start_serves_an_existing_directory_without_wiping_it(root):
    """The regression this replaces: start() used to rmtree the target and
    re-download from S3. With the builder writing into that same directory,
    that would delete a live build."""
    target = _seed_build_dir(root)
    marker = target / "AGENT_WORK_IN_PROGRESS.txt"
    marker.write_text("do not delete me", encoding="utf-8")

    host = ProtoHost(root=root, port_range=range(4001, 4010))
    info = await host.start(PID, SLUG)
    try:
        assert info.state == "running"
        assert marker.read_text(encoding="utf-8") == "do not delete me"
    finally:
        await host.stop(PID, SLUG)


async def test_start_404s_when_the_directory_does_not_exist(root):
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    with pytest.raises(FileNotFoundError):
        await host.start(PID, "never-built")


async def test_port_reservation_prevents_two_hosts_picking_one_port(root):
    """The old scanner closed its probe socket before spawning, so two
    concurrent starts could pick the same port. Reservations are recorded in
    the registry and skipped by later scans."""
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    for slug in ("a", "b"):
        _seed_build_dir(root, slug=slug)
    try:
        first = await host.start(PID, "a")
        second = await host.start(PID, "b")
        assert first.port != second.port
    finally:
        await host.stop(PID, "a")
        await host.stop(PID, "b")


async def test_start_writes_a_pid_file_and_removes_it_on_stop(root):
    target = _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    await host.start(PID, SLUG)
    pid_file = target / ".proto-host.pid"
    assert pid_file.is_file()
    assert pid_file.read_text(encoding="utf-8").strip().isdigit()

    await host.stop(PID, SLUG)
    assert not pid_file.exists()


def test_sweep_orphans_removes_stale_pid_files(root):
    """Backend restart leaves the previous run's children behind -- this is the
    replacement for the orphan-VM sweep that went away with the VM layer. A pid
    that no longer exists just has its file cleaned up."""
    target = _seed_build_dir(root)
    (target / ".proto-host.pid").write_text("99999999", encoding="utf-8")

    host = ProtoHost(root=root, port_range=range(4001, 4010))
    swept = host.sweep_orphans()

    assert swept == 1
    assert not (target / ".proto-host.pid").exists()
