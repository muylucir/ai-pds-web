# backend/tests/test_proto_host.py
from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest

from aipds.proto.host import ProtoHost

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


def _seed_env_probe_dir(root: Path, base_path: str,
                        pid: str = PID, slug: str = SLUG) -> Path:
    """A stub whose `build` script records the basePath env vars it was given,
    and whose `start` serves that recording. Lets a test assert what the BUILD
    step saw -- the only moment that matters, since Next.js bakes basePath into
    the output at build time."""
    target = root / pid / slug
    target.mkdir(parents=True, exist_ok=True)
    (target / "package.json").write_text(
        '{"name": "probe", "scripts": {'
        '"build": "node build.js", "start": "node server.js"}}',
        encoding="utf-8")
    (target / "build.js").write_text(
        "const fs = require('fs');\n"
        "fs.writeFileSync('built.txt', JSON.stringify({\n"
        "  NEXT_PUBLIC_BASE_PATH: process.env.NEXT_PUBLIC_BASE_PATH ?? null,\n"
        "  PROTO_BASE_PATH: process.env.PROTO_BASE_PATH ?? null,\n"
        "  BEDROCK_MODEL_ID: process.env.BEDROCK_MODEL_ID ?? null,\n"
        "}));\n",
        encoding="utf-8")
    (target / "server.js").write_text(
        "const fs = require('fs');\n"
        "const http = require('http');\n"
        "const body = fs.readFileSync('built.txt', 'utf-8');\n"
        "http.createServer((_, res) => {\n"
        "  res.writeHead(200, {'Content-Type': 'application/json'});\n"
        "  res.end(body);\n"
        "}).listen(process.env.PORT);\n",
        encoding="utf-8")
    return target


async def test_build_receives_the_base_path_env(root):
    """The build step must be told the public prefix.

    Next.js bakes `basePath` into its output at BUILD time, so injecting the
    prefix only at start (as PORT is) would be too late -- the asset URLs are
    already fixed. Before this, ProtoHost injected PORT and nothing else, which
    is why a hosted prototype's assets resolved against the CloudFront root
    (/_next/static/... -> 404) instead of /proto/{pid}/{slug}/_next/....
    """
    _seed_env_probe_dir(root, base_path=f"/proto/{PID}/{SLUG}")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG, base_path=f"/proto/{PID}/{SLUG}")

    try:
        assert info.state == "running", info.log_tail
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{info.port}/")
        # basePath 두 키만 본다 — 프로브는 다른 주입 값(BEDROCK_MODEL_ID)도
        # 함께 기록하므로, 전체 dict를 고정하면 주입이 하나 늘 때마다 이
        # 테스트가 무관하게 깨진다.
        seen = resp.json()
        assert seen["NEXT_PUBLIC_BASE_PATH"] == f"/proto/{PID}/{SLUG}"
        assert seen["PROTO_BASE_PATH"] == f"/proto/{PID}/{SLUG}"
    finally:
        await host.stop(PID, SLUG)


async def test_build_env_absent_when_no_base_path_given(root):
    """`base_path` stays optional: a caller that does not pass one (tests, a
    non-proxied prototype) must not get empty-string env vars, which a
    next.config.js reading `process.env.NEXT_PUBLIC_BASE_PATH ?? ''` cannot
    distinguish from an intentional root deployment."""
    _seed_env_probe_dir(root, base_path="")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG)

    try:
        assert info.state == "running", info.log_tail
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{info.port}/")
        seen = resp.json()
        assert seen["NEXT_PUBLIC_BASE_PATH"] is None
        assert seen["PROTO_BASE_PATH"] is None
    finally:
        await host.stop(PID, SLUG)


async def test_build_and_start_receive_the_projects_model(root):
    """프로토타입 **앱 자체**가 런타임에 쓸 모델도 프로젝트 설정을 따라야 한다.

    종전에는 Discovery와 빌드 에이전트만 project_model()을 상속받았고
    (app.py의 두 자리), 빌드된 앱은 에이전트가 자기 `.env.example`에 적어 둔
    값으로 돌았다 — 실물에서
    `BEDROCK_MODEL_ID=apac.anthropic.claude-sonnet-4-5-...`가 하드코딩된 채
    프로젝트는 다른 모델로 설정돼 있었다.

    빌드 단계에서 확인하는 이유: 프레임워크가 `process.env.*`를 빌드 시점에
    인라인할 수 있어서, start에만 있는 값은 undefined로 굳는다."""
    _seed_env_probe_dir(root, base_path="")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG, model_id="global.anthropic.claude-opus-5")

    try:
        assert info.state == "running", info.log_tail
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{info.port}/")
        assert resp.json()["BEDROCK_MODEL_ID"] == "global.anthropic.claude-opus-5"
    finally:
        await host.stop(PID, SLUG)


async def test_model_env_absent_when_no_model_given(root):
    """`model_id`는 선택이다. 넘기지 않으면 빈 문자열이 아니라 **없어야** 한다 —
    빈 값을 주면 앱이 "설정됐지만 빈 모델"로 읽어 Bedrock 호출이 알기 어려운
    에러로 실패한다. base_path가 같은 이유로 선택인 것과 같은 규율이다."""
    _seed_env_probe_dir(root, base_path="")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    info = await host.start(PID, SLUG)

    try:
        assert info.state == "running", info.log_tail
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{info.port}/")
        assert resp.json()["BEDROCK_MODEL_ID"] is None
    finally:
        await host.stop(PID, SLUG)


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


async def test_status_does_not_read_the_log(root, monkeypatch):
    """`status()`는 로그 파일을 **열지 않는다**. 목록 라우트가 프로토타입마다
    이걸 부르는데, `_tail_text`는 마지막 100줄을 얻으려고 파일을 전부 읽고
    (`read_text()`) `.proto-host.log`는 회전이 없다. 실측: 100MB에서 호출당
    237ms, 그것도 async 함수 안의 동기 I/O라 그동안 모든 SSE 스트림이 멈춘다.

    `_tail_text` 호출 자체를 세는 이유: `log_tail == ""`만 검사하면 로그를
    읽고 나서 버리는 구현도 통과하는데, 비용은 읽는 데 있지 담는 데 있지
    않다."""
    import aipds.proto.host as host_mod

    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    info = await host.start(PID, SLUG)
    try:
        assert info.state == "running"

        calls = []
        real = host_mod._tail_text
        monkeypatch.setattr(host_mod, "_tail_text",
                            lambda p, n: (calls.append(p), real(p, n))[1])

        status = host.status(PID, SLUG)
        assert status is not None
        assert status.state == "running"
        assert status.port == info.port
        assert calls == [], f"status() read the log: {calls}"
        assert status.log_tail == ""

        # 반면 사용자가 명시적으로 요청하는 경로는 여전히 읽어야 한다.
        assert isinstance(host.log_tail(PID, SLUG), str)
        assert len(calls) == 1
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


def test_slugs_unions_dirs_registry_and_token_cache(root):
    """프로젝트 삭제가 "이 프로젝트의 모든 슬러그"를 여기서 얻는다. 디스크
    디렉토리만 보면 토큰만 발급된 경계 상태를 놓치고, 그 슬러그의 토큰이
    인메모리에 남아 삭제 뒤에도 링크가 풀린다."""
    _seed_build_dir(root, slug="on-disk")
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    # 토큰만 있는 슬러그(디렉토리는 ensure_token이 만든다)
    host.ensure_token(PID, "token-only")
    # 다른 프로젝트는 섞이지 않는다.
    _seed_build_dir(root, pid="other-proj", slug="not-mine")

    assert host.slugs(PID) == ["on-disk", "token-only"]
    assert host.slugs("other-proj") == ["not-mine"]
    assert host.slugs("never-existed") == []


def test_slugs_rejects_traversal_pid(root):
    """`rmtree` 대상 경로를 만드는 값이므로 여기서도 한 세그먼트만 받는다."""
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    with pytest.raises(ValueError):
        host.slugs("..")


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


async def test_reserved_port_is_released_when_the_start_spawn_raises(root, monkeypatch):
    """Regression: if the final `npm start` spawn itself raises (npm missing
    from PATH, EMFILE, ...) rather than exiting nonzero, entry.port never gets
    assigned -- so stop()'s `if entry.port is not None` release could never
    fire, and the port stayed reserved for the ProtoHost's whole lifetime."""
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    real_exec = asyncio.create_subprocess_exec

    async def boom(program, *args, **kwargs):
        if program == "npm" and "env" in kwargs:
            # Only the final start-spawn passes `env=` (the install/build
            # calls via _run_npm do not) -- fail exactly that call.
            raise OSError("simulated spawn failure")
        return await real_exec(program, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

    with pytest.raises(OSError):
        await host.start(PID, SLUG)

    assert host._reserved == set()

    # The port must be obtainable again by a subsequent start(), not
    # permanently walled off.
    monkeypatch.setattr(asyncio, "create_subprocess_exec", real_exec)
    info = await host.start(PID, SLUG)
    try:
        assert info.state == "running"
        assert info.port in range(4001, 4010)
    finally:
        await host.stop(PID, SLUG)


# ---- purge ----

async def test_purge_removes_the_build_tree(root):
    _seed_build_dir(root)
    (root / PID / SLUG / "prototype").mkdir(parents=True)
    (root / PID / SLUG / "prototype" / "package.json").write_text("{}",
                                                                 encoding="utf-8")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    await host.purge(PID, SLUG)

    assert not (root / PID / SLUG).exists()


async def test_purge_leaves_other_prototypes_alone(root):
    _seed_build_dir(root, slug="keep-me")
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    await host.purge(PID, SLUG)

    assert not (root / PID / SLUG).exists()
    assert (root / PID / "keep-me").is_dir()


async def test_purge_stops_a_running_process_first(root):
    """Deleting the tree out from under a live `npm start` would leave an
    orphan process holding the port. purge stops it first."""
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    info = await host.start(PID, SLUG)
    assert info.state == "running", info.log_tail

    await host.purge(PID, SLUG)

    assert host.status(PID, SLUG) is None
    assert not (root / PID / SLUG).exists()


async def test_purge_is_idempotent(root):
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    await host.purge(PID, SLUG)
    await host.purge(PID, SLUG)


async def test_purge_raises_when_residue_survives_the_sweep(root):
    """rmtree(ignore_errors=True) swallows failures instead of raising, so a
    permission error deep in the tree (e.g. inside node_modules) can leave
    residue on disk while looking like success. purge() must not return
    cleanly in that case -- the reset route relies on a raise to know the
    tree needs a retry, and with S3 state already purged by then, a swallowed
    failure here would leave the prototype's card claiming "built" forever
    over a half-deleted tree."""
    target = _seed_build_dir(root)
    blocked = target / "node_modules" / "stuck-pkg"
    blocked.mkdir(parents=True)
    (blocked / "file.txt").write_text("stuck", encoding="utf-8")
    # Removing a file needs write+execute on its CONTAINING directory, not on
    # the file itself -- stripping write from `blocked` makes file.txt (and
    # therefore `blocked` itself, now non-empty forever) undeletable.
    blocked.chmod(0o500)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    try:
        with pytest.raises(RuntimeError):
            await host.purge(PID, SLUG)
        # The residue is the whole point of the test: something must survive
        # for the raise to be meaningful rather than a false alarm.
        assert target.exists()
        assert (blocked / "file.txt").exists()
    finally:
        blocked.chmod(0o700)


@pytest.mark.parametrize("bad_slug", ["..", ".", "", "a/b", "/etc"])
async def test_purge_refuses_a_slug_that_is_not_one_path_segment(root, bad_slug):
    """The one that actually deletes things off disk, so it validates its own
    input rather than trusting the caller.

    `pathlib` does not normalise: `root / pid / ".."` really is `root`'s
    parent, so `rmtree` there takes EVERY project's build tree, and `"."`
    resolves to `root / pid` and takes every sibling prototype of one project.
    Both were reproducible before the guard (`purge("me", "..")` emptied the
    whole root) and both answer 204 through the route.
    """
    _seed_build_dir(root, pid="victim", slug="theirs")
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    with pytest.raises(ValueError):
        await host.purge(PID, bad_slug)

    # Nothing was touched -- the raise happens before stop() or rmtree.
    assert (root / "victim" / "theirs").is_dir()
    assert (root / PID / SLUG).is_dir()


@pytest.mark.parametrize("bad_pid", ["..", ".", "", "a/b"])
async def test_purge_refuses_an_unsafe_pid_too(root, bad_pid):
    """`{root}/{pid}/{slug}` has two attacker-supplied segments, and a guard on
    only the second leaves `root / ".." / slug` reachable."""
    _seed_build_dir(root, pid="victim", slug="theirs")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    with pytest.raises(ValueError):
        await host.purge(bad_pid, SLUG)

    assert (root / "victim" / "theirs").is_dir()


# ---- purge_project: 부모 디렉터리 ----

async def test_purge_project_removes_the_parent_shell(root):
    """`purge`는 `{root}/{pid}/{slug}`만 지우므로 부모가 빈 껍데기로 남는다.

    실측(2026-08-19, 배포 인스턴스): 존재하지 않는 프로젝트 3개의 빈 디렉터리가
    `/opt/pathfinder/protos/`에 남아 있었다.
    """
    _seed_build_dir(root, pid=PID, slug=SLUG)
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    await host.purge(PID, SLUG)
    assert (root / PID).is_dir(), "전제 확인 — purge는 부모를 남긴다"

    await host.purge_project(PID)

    assert not (root / PID).exists()


async def test_purge_project_is_idempotent(root):
    """없는 프로젝트는 no-op이다 — 재시도가 수렴해야 하고, 프로토타입을 한 번도
    만들지 않은 프로젝트의 삭제가 흔한 경우다."""
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    await host.purge_project("never-built")
    await host.purge_project("never-built")


async def test_purge_project_leaves_other_projects_alone(root):
    _seed_build_dir(root, pid="victim", slug="theirs")
    _seed_build_dir(root, pid=PID, slug=SLUG)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    await host.purge_project(PID)

    assert not (root / PID).exists()
    assert (root / "victim" / "theirs").is_dir()


@pytest.mark.parametrize("bad_pid", ["..", ".", "", "a/b"])
async def test_purge_project_refuses_an_unsafe_pid(root, bad_pid):
    """`rmtree`가 하나의 URL 파라미터를 디렉터리 이름으로 받는 자리다.

    `pathlib`은 정규화하지 않으므로 `root / ".."`는 정말로 root의 부모이고
    `root / "."`는 root 자신이다 — 검증이 없으면 한 프로젝트 삭제가 **모든**
    프로젝트의 빌드 트리를 지운다. `purge`가 같은 이유로 같은 가드를 갖는다.
    """
    _seed_build_dir(root, pid="victim", slug="theirs")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    with pytest.raises(ValueError):
        await host.purge_project(bad_pid)

    assert (root / "victim" / "theirs").is_dir()
