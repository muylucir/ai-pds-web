# backend/tests/test_proto_host.py
from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest

from aipds.proto.host import TOKEN_FILENAME, ProtoHost

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


def _state(root: Path) -> Path:
    """ProtoHost's default state tree — next to the build root."""
    return root.with_name(root.name + "-state")


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
        # Check only the two basePath keys — the probe also records other
        # injected values (BEDROCK_MODEL_ID), so pinning the whole dict would
        # break this test for unrelated reasons every time an injection is added.
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
    """The model the prototype **app itself** uses at runtime must also follow the project setting.

    Previously only Discovery and the build agent inherited project_model()
    (two places in app.py), and the built app ran on whatever value the agent
    had written into its own `.env.example` — in a real case
    `BEDROCK_MODEL_ID=apac.anthropic.claude-sonnet-4-5-...` was hardcoded while
    the project was configured for a different model.

    Why check at the build step: a framework can inline `process.env.*` at build
    time, so a value present only at start gets frozen as undefined."""
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
    """`model_id` is optional. When not passed it must be **absent**, not an empty
    string — an empty value makes the app read it as "configured but empty model",
    and the Bedrock call fails with an obscure error. Same discipline as base_path
    being optional for the same reason."""
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


@pytest.mark.parametrize("block", [1, 7, 64, 64 * 1024])
def test_tail_text_matches_reading_the_whole_file(tmp_path, monkeypatch, block):
    """Reading backward from the end must give the same answer as reading the whole file and slicing.

    Shrinking the block size forces the edge cases — a block cut in the middle of a
    line, in the middle of a multibyte character, or right before/after a newline.
    With the production block (64KB) a small log fits in one block and these paths
    never show up.
    """
    import random
    import aipds.proto.host as host_mod

    monkeypatch.setattr(host_mod, "_TAIL_BLOCK", block)
    rng = random.Random(block)
    # A lone `\r` is really used by npm's progress bar — splitlines treats it as a line break too.
    alphabet = ["a", "b", "한", "글", " ", "\n", "\n", "\r\n", "\r"]
    log = tmp_path / ".proto-host.log"
    for _ in range(200):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 80)))
        log.write_bytes(text.encode("utf-8"))
        n = rng.randrange(1, 12)
        expected = "\n".join(text.splitlines()[-n:])
        assert host_mod._tail_text(log, n) == expected, (text, n)


def test_tail_text_of_a_missing_log_is_empty(tmp_path):
    from aipds.proto.host import _tail_text
    assert _tail_text(tmp_path / "absent.log", 100) == ""


async def test_status_does_not_read_the_log(root, monkeypatch):
    """`status()` does **not open** the log file. The list route calls it for every
    prototype, and that list is polled. `_tail_text` reads only the end of the file,
    but it is synchronous disk I/O inside an async function, and there is no reason
    to read it where only the state is needed.

    Why count the `_tail_text` calls themselves: checking only `log_tail == ""`
    would also pass an implementation that reads the log and then discards it, but
    the cost is in the reading, not in the storing."""
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

        # The path the user explicitly requests, on the other hand, must still read it.
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

    try:
        await host.start(PID, SLUG)
        pid_file = _state(root) / PID / SLUG / "host.pid"
        assert pid_file.is_file()
        assert pid_file.read_text(encoding="utf-8").strip().isdigit()
    finally:
        await host.stop(PID, SLUG)
    assert not pid_file.exists()
    assert not (target / ".proto-host.pid").exists()


async def test_hosting_bookkeeping_stays_out_of_the_trees_the_sandbox_writes(root):
    """Log, pid, and token are created only in the state tree. The build tree is written by
    the build agent and `prototype/` under it by npm scripts — if these files lived there,
    they could be swapped for links to make the backend write beyond them (log) and read
    someone else's token (token)."""
    target = _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    try:
        await host.start(PID, SLUG)
        token = host.ensure_token(PID, SLUG)
    finally:
        await host.stop(PID, SLUG)

    state = _state(root) / PID / SLUG
    assert (state / "host.log").is_file()
    assert (state / "token").read_text(encoding="utf-8") == token
    sandbox_written = {p.name for p in root.rglob("*")}
    assert not sandbox_written & {".proto-host.log", ".proto-host.pid", TOKEN_FILENAME,
                                  "host.log", "host.pid", "token"}
    assert target.is_dir()


def test_a_legacy_token_file_moves_to_the_state_tree(root):
    """A token in the old location (directly under the build tree) is moved to the state tree
    at startup — links already handed out keep working."""
    _seed_build_dir(root)
    (root / PID / SLUG / TOKEN_FILENAME).write_text("legacy-tok", encoding="utf-8")
    host = ProtoHost(root=root)

    assert host.load_tokens() == 1
    assert host.resolve_token("legacy-tok") == (PID, SLUG)
    assert (_state(root) / PID / SLUG / "token").read_text(encoding="utf-8") == "legacy-tok"
    assert not (root / PID / SLUG / TOKEN_FILENAME).exists()


def test_a_legacy_token_symlink_is_not_followed(root):
    """If the token file in the old location is a link to another project's token, it is not
    imported — importing it would make someone else's share link open this prototype."""
    _seed_build_dir(root)
    _seed_build_dir(root, pid="other-proj", slug="app")
    (root / "other-proj" / "app" / TOKEN_FILENAME).write_text("other-tok", encoding="utf-8")
    (root / PID / SLUG / TOKEN_FILENAME).symlink_to(root / "other-proj" / "app" / TOKEN_FILENAME)
    host = ProtoHost(root=root)

    host.load_tokens()
    assert host.resolve_token("other-tok") == ("other-proj", "app")
    assert host.token_for(PID, SLUG) is None


def test_slugs_unions_dirs_registry_and_token_cache(root):
    """Project deletion gets "every slug of this project" from here. Looking only at
    disk directories misses the edge state where only a token was issued, and that
    slug's token stays in memory, so the link still resolves after deletion."""
    _seed_build_dir(root, slug="on-disk")
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    # A slug with only a token (ensure_token creates the directory)
    host.ensure_token(PID, "token-only")
    # Other projects do not get mixed in.
    _seed_build_dir(root, pid="other-proj", slug="not-mine")

    assert host.slugs(PID) == ["on-disk", "token-only"]
    assert host.slugs("other-proj") == ["not-mine"]
    assert host.slugs("never-existed") == []


def test_slugs_rejects_traversal_pid(root):
    """This value builds an `rmtree` target path, so only a single segment is accepted here too."""
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
        if program == "npm" and kwargs.get("start_new_session"):
            # Only the final start-spawn asks for its own session -- fail
            # exactly that call, and let install/build through. The earlier
            # discriminator here was `"env" in kwargs`, which install and build
            # also pass: the OSError landed on the install spawn, `start()`
            # returned before a port was ever reserved, and the assertion below
            # passed without exercising the release path it exists to guard.
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


# ---- purge_project: the parent directory ----

async def test_purge_project_removes_the_parent_shell(root):
    """`purge` deletes only `{root}/{pid}/{slug}`, so the parent is left as an empty shell.

    Observed (2026-08-19, deployed instance): empty directories for 3 projects that no
    longer existed remained in `/opt/aipds/protos/`.
    """
    _seed_build_dir(root, pid=PID, slug=SLUG)
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    await host.purge(PID, SLUG)
    assert (root / PID).is_dir(), "전제 확인 — purge는 부모를 남긴다"

    await host.purge_project(PID)

    assert not (root / PID).exists()


async def test_purge_project_is_idempotent(root):
    """A nonexistent project is a no-op — retries must converge, and deleting a project
    that never built a prototype is the common case."""
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
    """This is where `rmtree` takes a single URL parameter as a directory name.

    `pathlib` does not normalize, so `root / ".."` really is root's parent and
    `root / "."` is root itself — without validation, deleting one project wipes the
    build trees of **every** project. `purge` has the same guard for the same reason.
    """
    _seed_build_dir(root, pid="victim", slug="theirs")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    with pytest.raises(ValueError):
        await host.purge_project(bad_pid)

    assert (root / "victim" / "theirs").is_dir()


# ---- Is the running server older than the source? ----
#
# **Why a timestamp is needed.** Once a running prototype could be modified, there came
# a window where the server stays up as is while only the source changes. During it the
# card says only "실행 :4007" (running :4007), so the user reads it as their change being
# live — in fact the link sent to participants shows the previous version, and it only
# changes after re-hosting.
#
# Making that call requires "when did this server read the source", but the registry had
# no timestamp at all. Substituting whether a session is open was wrong: the signal
# disappears the moment the session closes, and that is exactly when the user is most
# likely to be misled.

async def test_a_started_host_records_when_it_read_the_source(root):
    host = ProtoHost(root=root)
    _seed_build_dir(root)
    try:
        info = await host.start(PID, SLUG)
        assert info.state == "running"
        assert info.built_at is not None
        # status() must return the same value — it is the path the list route reads.
        assert host.status(PID, SLUG).built_at == info.built_at
    finally:
        await host.stop(PID, SLUG)


async def test_the_timestamp_precedes_the_build_step(root):
    """It must be the time **before** the build reads the source. Taking it after the build
    finishes would wrongly judge files changed mid-build as included (the build did not
    read those changes).

    Conversely, taking it before npm install is also wrong: install takes minutes in
    practice, and files changed in that window are actually read by the following build,
    so they are not stale."""
    import time

    host = ProtoHost(root=root)
    target = _seed_build_dir(root)
    before = time.time()
    try:
        info = await host.start(PID, SLUG)
        after = time.time()
        assert info.built_at is not None
        assert before <= info.built_at <= after
        # And the build tree's **source** is older than that — i.e., the server that
        # just came up is not stale. Going through the exclusion rules is part of the
        # right answer: hosting itself writes `.proto-host.log`/`.pid` after
        # `built_at`, so comparing raw mtimes would always report a freshly started
        # server as stale (this test first failed exactly that way).
        from aipds.proto.source import newest_source_mtime
        newest = newest_source_mtime(target)
        assert newest is not None and newest <= info.built_at
    finally:
        await host.stop(PID, SLUG)


async def test_an_unstarted_host_has_no_timestamp(root):
    host = ProtoHost(root=root)
    assert host.status(PID, SLUG) is None


def test_sweep_finds_pid_files_where_hosting_actually_writes_them(root):
    """Hosting runs in `prototype/` **inside** the build tree — the pid file is at that depth too.
    While the sweep looked only one level up, it found nothing."""
    host = ProtoHost(root=root)
    served = root / "p1" / "demo" / "prototype"
    served.mkdir(parents=True)
    (served / ".proto-host.pid").write_text("99999999", encoding="utf-8")

    assert host.previously_running() == [("p1", "demo")]
    assert host.sweep_orphans() == 1
    assert not (served / ".proto-host.pid").exists()
    assert host.previously_running() == []
