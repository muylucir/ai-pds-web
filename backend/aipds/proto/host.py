# backend/aipds/proto/host.py — ProtoHost: EC2-local prototype hosting.
#
# Task 5's PrototypeSession (the in-process builder) writes the prototype
# straight into `{root}/{pid}/{slug}/` on this same box, so hosting no longer
# downloads a bundle from S3 -- it just runs the npm lifecycle (install ->
# build? -> start) as a real subprocess against that existing directory,
# scanning a free local port for it to listen on. Independent of the
# MicroVM/harness side entirely -- this is the "run the finished thing on the
# backend box" half of the feature (Task 7's routes call it).
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from aipds import agent_home
from aipds.pathsafe import read_text_beneath, reject_unsafe_segment, stat_beneath

if TYPE_CHECKING:
    from aipds.launcher import Launcher

HostState = Literal["installing", "building", "running", "failed", "stopped"]

#: Hosting bookkeeping (access token, log, pid) lives in the **state tree**, not the build
#: tree: `{state_root}/{pid}/{slug}/`. The state tree is not bound into any sandbox.
#:
#: The build tree (`{root}/{pid}/{slug}/`) is written by the build agent, and `prototype/`
#: beneath it by the prototype's npm scripts. Keeping bookkeeping there opens two holes:
#:
#: - Replace the log with a link pointing outside, and the backend appends npm output past
#:   it as the backend uid (the npm scripts choose the output, so it is an arbitrary write).
#: - Replace the token file with a link to another project's token file, and the backend
#: reads
#:   that token as this prototype's, so someone else's share link points at this prototype.
#:
#: Keeping the token outside `prototype/` was needed even before that — it is the served
#: tree
#: and the tree "download" bundles, so a token there goes out to exactly the people it is
#: meant to block.
#:
#: `TOKEN_FILENAME` is the name at the old location (directly under the build tree). Export
#: and download may still bundle trees where that name remains, so it stays excluded
#: (project_bundle), and on startup it is moved into the state tree (`load_tokens`).
#:
#: A filename, not a credential -- the `nosec` is for the name, which is all
#: bandit B105 looks at.
TOKEN_FILENAME = ".proto-token"  # nosec B105
_STATE_TOKEN = "token"  # nosec B105
_STATE_LOG = "host.log"
_STATE_PID = "host.pid"

#: 32 bytes -> 43 urlsafe chars, the same strength as a survey token
#: (`routes/surveys.py`'s TOKEN_BYTES). Deliberately identical: two kinds of
#: public link with two different strengths invites the question "why", and the
#: honest answer would be "no reason".
TOKEN_BYTES = 32


@dataclass
class HostInfo:
    state: HostState
    port: int | None
    log_tail: str
    #: When this server read its sources (epoch seconds). None if it has never started.
    #:
    #: **What it is for.** Once a running prototype could be modified, there came a window
    #: where the server stays up while only the sources change. Comparing this value with
    #: the
    #: build tree's latest mtime lets us say "what is running is the previous version" —
    #: without it, the card only says "running :4007" and the user reads that the change is
    #: live.
    built_at: float | None = None


@dataclass
class _HostEntry:
    """Internal registry record -- not part of the public interface."""
    dir: Path
    log_path: Path
    state: HostState
    port: int | None = None
    proc: "asyncio.subprocess.Process | None" = None
    #: Source of HostInfo.built_at — see the comment there for the rationale.
    built_at: float | None = None
    #: Whether it was started via the launch wrapper. If so, `proc` is sudo (→ the
    #: systemd-run
    #: client) and signals do not stop the server — `stop` has the wrapper stop the unit.
    launched: bool = False


#: Unit for reading the tail. One block usually holds 100 lines of npm output.
_TAIL_BLOCK = 64 * 1024


def _tail_text(path: Path, lines: int) -> str:
    """The last `lines` lines of the log. Reads backward from the end of the file only as
    much as needed.

    Hosting logs grow append-only without rotation ("ab" in `_npm_install`/
    `_npm_run_build`) — repeated hosting grows them to tens to hundreds of MB. Reading it
    all
    costs time proportional to file size per call (measured 100MB → 237ms), and this
    function
    is called synchronously inside an async route, so every SSE stream stalls for that long.

    It slices by bytes and then decodes, so a multibyte character cut at a block boundary
    can
    only occur on the first line, and that line is dropped (except when reading reached the
    very start of the file — nothing was cut).
    """
    try:
        f = path.open("rb")
    except FileNotFoundError:
        return ""
    with f:
        end = f.seek(0, 2)
        pos = end
        data = b""
        # Until we see line count + 1 newlines — the leading fragment may be a cut line.
        while pos > 0 and data.count(b"\n") <= lines:
            step = min(_TAIL_BLOCK, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    all_lines = data.decode("utf-8", errors="replace").splitlines()
    if pos > 0:
        all_lines = all_lines[1:]
    return "\n".join(all_lines[-lines:])


class ProtoHost:
    """Runs a built prototype bundle as a local npm subprocess.

    Registry is an in-memory dict keyed by (pid, slug) -- this host does not
    persist across process restarts (matches the VM-side session's lifetime
    assumptions; a restart loses the running subprocess anyway).
    """

    # 4000-7999. Both ends matter: 3000 is the frontend dev server and 8000 is
    # this backend, so the range deliberately stops short of 8000 rather than
    # extending to a round number. Wide (4000 ports) even though
    # AIPDS_PROTO_MAX_CONCURRENT caps live builds at 2 -- `_scan_port`
    # probes sequentially and skips anything already bound, so leftovers from a
    # previous process cost a few probes, not a wedged start.
    def __init__(self, root: Path, port_range: range = range(4000, 8000),
                 launcher: "Launcher | None" = None,
                 state_root: Path | None = None):
        # No `s3`: the build directory IS the served tree now (the builder
        # writes straight into it), so hosting no longer round-trips a bundle
        # through S3 -- which also means binary assets stop being mangled by
        # the text-only store.
        self._root = Path(root)
        #: The bookkeeping tree (see the description above TOKEN_FILENAME). Defaults to next
        #: to
        #: the build root — in deployment it is `/opt/aipds/protos-state`, and the sandbox
        #: masks all of `/opt/aipds`.
        self._state_root = (Path(state_root) if state_root is not None
                            else self._root.with_name(self._root.name + "-state"))
        self._port_range = port_range
        #: Launch wrapper (aipds/launcher.py). If present, npm runs as the aipds-proto uid
        #: in a
        #: sandbox that sees only this prototype's `prototype/` tree. If absent, directly as
        #: the backend uid.
        self._launcher = launcher
        self._registry: dict[tuple[str, str], _HostEntry] = {}
        # Ports handed out but whose subprocess may not be listening yet. The
        # scanner's bind probe releases its socket before the spawn, so two
        # concurrent starts could otherwise pick the same port.
        self._reserved: set[int] = set()
        # token -> (pid, slug). A CACHE over the `.proto-token` files, not the
        # source of truth: `load_tokens()` rebuilds it from disk at startup so
        # links already handed out at a workshop survive a backend restart.
        # (The registry above deliberately does NOT survive one -- a restart
        # kills the npm subprocess, so there is no running host to remember.
        # Tokens are different: the file outlives the process, and re-hosting
        # must not invalidate a link someone already pasted into chat.)
        self._tokens: dict[str, tuple[str, str]] = {}

    # ---- access tokens ----

    def _state_dir(self, pid: str, slug: str) -> Path:
        reject_unsafe_segment(pid)
        reject_unsafe_segment(slug)
        return self._state_root / pid / slug

    def _token_path(self, pid: str, slug: str) -> Path:
        return self._state_dir(pid, slug) / _STATE_TOKEN

    def _write_state(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(text, encoding="utf-8")

    def _adopt_legacy_tokens(self) -> None:
        """Move a token file at the old location (directly under the build tree) into the
        state tree.

        The old file sat where the build agent could write, so it is read without following
        links — if it were a link, another project's token would be imported as this
        prototype's.
        """
        if not self._root.is_dir():
            return
        for legacy in self._root.glob(f"*/*/{TOKEN_FILENAME}"):
            slug_dir = legacy.parent
            pid, slug = slug_dir.parent.name, slug_dir.name
            rel = f"{pid}/{slug}/{TOKEN_FILENAME}"
            try:
                target = self._token_path(pid, slug)
                if stat_beneath(self._root, rel) is not None and not target.exists():
                    token = read_text_beneath(self._root, rel).strip()
                    if token:
                        self._write_state(target, token)
                legacy.unlink()
            except (OSError, ValueError):
                continue

    def load_tokens(self) -> int:
        """Rebuild the token cache from disk. Returns how many were found.

        Called at startup alongside `sweep_orphans` (same glob shape, same
        best-effort spirit). Without this, a backend restart would 404 every
        link already distributed -- and unlike a lost subprocess, that is not
        something re-hosting silently repairs: the participant's URL contains
        the old token forever.

        Unreadable or empty files are skipped rather than raised on. A token
        file we cannot read is a link that cannot work no matter what we do
        here; failing startup over it would take the whole backend down for one
        prototype's broken link.
        """
        self._tokens.clear()
        self._adopt_legacy_tokens()
        if not self._state_root.is_dir():
            return 0
        for token_file in self._state_root.glob(f"*/*/{_STATE_TOKEN}"):
            try:
                token = token_file.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not token:
                continue
            slug_dir = token_file.parent
            self._tokens[token] = (slug_dir.parent.name, slug_dir.name)
        return len(self._tokens)

    def ensure_token(self, pid: str, slug: str) -> str:
        """This prototype's access token, creating one only if absent.

        Reuse is the point. Hosting gets stopped and restarted routinely during
        a workshop, and minting a fresh token on each start would silently kill
        every link already shared -- the failure would surface as participants
        reporting a 404 on a URL that worked ten minutes ago. The deliberate
        way to revoke is the reset button, whose `purge()` deletes the tree and
        the token with it.
        """
        path = self._token_path(pid, slug)
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            # Re-seed the cache: a restart's load_tokens() may have missed this
            # (e.g. the dir appeared afterwards), and the file is authoritative.
            self._tokens[existing] = (pid, slug)
            return existing
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._write_state(path, token)
        self._tokens[token] = (pid, slug)
        return token

    def adopt_token(self, token: str, pid: str, slug: str) -> None:
        """Plant a token found elsewhere (the S3 index, proto/store.py) into the cache and
        the token file.

        A replaced instance has no token file, so the cache is empty too. Planting the value
        found in the index here when a link comes in makes the disk paths (`token_for`,
        `ensure_token`) work as-is from then on — it is the same value, so links already
        handed out do not change.
        """
        self._write_state(self._token_path(pid, slug), token)
        self._tokens[token] = (pid, slug)

    def token_for(self, pid: str, slug: str) -> str | None:
        """This prototype's token if it has one, WITHOUT minting one.

        Read paths (the list route, host status) use this: a GET must not have
        the side effect of creating a credential.
        """
        try:
            token = self._token_path(pid, slug).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if token:
            self._tokens[token] = (pid, slug)
        return token or None

    def resolve_token(self, token: str) -> tuple[str, str] | None:
        """token -> (pid, slug), or None if it resolves to nothing.

        Compared in constant time against the cache's keys. A plain dict lookup
        would leak timing on the hash comparison, and while that is a thin
        channel over HTTP, the mitigation costs one loop over a dict that holds
        one entry per prototype.
        """
        for known, target in self._tokens.items():
            if secrets.compare_digest(known, token):
                return target
        return None

    # ---- internals ----

    @staticmethod
    def _info(entry: _HostEntry, *, with_log: bool = True) -> HostInfo:
        """`with_log=False` captures only the state, without reading the log.

        `_tail_text` reads only the end of the file, but it is still **synchronous disk
        I/O** inside an async function, and the list route calls `status()` once per
        prototype, and that list is polled. Where only the state is needed there is no
        reason to read the log, so `status()` does not read it.

        The two places that actually need the log are left as they are: failure diagnosis in
        `start()` (it goes out as the 502 detail) and `log_tail()` (when the user clicks
        "view log"). Both are called rarely, and the user is waiting for that content.
        """
        return HostInfo(
            state=entry.state, port=entry.port,
            built_at=entry.built_at,
            log_tail=_tail_text(entry.log_path, 100) if with_log else "")

    @staticmethod
    def _read_package_json(target_dir: Path) -> dict:
        # A tree the prototype writes — do not follow links (pathsafe).
        try:
            return json.loads(read_text_beneath(target_dir, "package.json"))
        except (OSError, ValueError):
            return {}

    def _env(self, kind: str, pid: str, slug: str,
             extra: dict[str, str]) -> dict[str, str]:
        if self._launcher is not None:
            return self._launcher.npm_env(kind, pid, slug, extra)
        return {**os.environ, **extra}

    async def _npm_install(self, pid: str, slug: str, cwd: Path, log_path: Path,
                           extra: dict[str, str]) -> int:
        """`npm install`, to completion, appending to the host log.

        One method per npm subcommand -- rather than one method taking argv --
        so that the subcommand is a **literal** at the spawn. Command-injection
        analysis reads `create_subprocess_exec("npm", *args)` as a non-static
        argv no matter how closed the caller's list is, and it judges the second
        positional argument specifically. Spelling it out keeps every argv this
        process can exec visible in the source, which is also the honest way to
        state what the earlier "trust me, the callers pass literals" comment was
        claiming. Nothing here comes from a request body: `cwd` is derived from
        a pid/slug `reject_unsafe_segment` has already validated, and no shell
        is involved.
        """
        env = self._env("host-build", pid, slug, extra)
        log_fh = open(log_path, "ab")
        try:
            if self._launcher is not None:
                proc = await self._launcher.exec_npm(
                    "host-build", pid, slug, "install",
                    cwd=str(cwd), stdout=log_fh, stderr=log_fh, env=env,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "npm", "install", cwd=str(cwd),
                    stdout=log_fh, stderr=log_fh, env=env,
                )
            return await proc.wait()
        finally:
            log_fh.close()

    async def _npm_run_build(self, pid: str, slug: str, cwd: Path, log_path: Path,
                             extra: dict[str, str]) -> int:
        """`npm run build`, to completion. Sibling of `_npm_install` -- the
        reason they are separate methods is in that docstring."""
        env = self._env("host-build", pid, slug, extra)
        log_fh = open(log_path, "ab")
        try:
            if self._launcher is not None:
                proc = await self._launcher.exec_npm(
                    "host-build", pid, slug, "run", "build",
                    cwd=str(cwd), stdout=log_fh, stderr=log_fh, env=env,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "npm", "run", "build", cwd=str(cwd),
                    stdout=log_fh, stderr=log_fh, env=env,
                )
            return await proc.wait()
        finally:
            log_fh.close()

    async def _wait_for_port(self, proc: "asyncio.subprocess.Process",
                              port: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if proc.returncode is not None:
                return False  # process died before ever accepting connections
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                pass
            else:
                writer.close()
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.2)

    def _scan_port(self) -> int:
        for port in self._port_range:
            if port in self._reserved:
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                self._reserved.add(port)
                return port
            except OSError:
                continue
            finally:
                sock.close()
        raise RuntimeError(f"no free port in {self._port_range}")

    # ---- public interface ----

    async def start(self, pid: str, slug: str, cwd: Path | None = None,
                    base_path: str | None = None,
                    model_id: str | None = None) -> HostInfo:
        """Run the npm lifecycle for one prototype and return its HostInfo.

        `base_path` is the public prefix the reverse proxy serves this prototype
        under (`/proto/{pid}/{slug}`). It is exported to BOTH the build and the
        start step, because Next.js bakes `basePath` into its output at build
        time -- injecting it only at start, the way PORT is, would come too late
        and leave the asset URLs pointing at the CloudFront root. Left unset,
        the env vars are absent rather than empty, so a config reading
        `process.env.NEXT_PUBLIC_BASE_PATH ?? ''` can still tell "no prefix"
        from "prefix I forgot to pass".

        `model_id` is the project's Bedrock model, so a prototype that calls an
        LLM at runtime uses the model the user picked for the project rather
        than one the build agent chose for itself. Without this the two drift:
        Discovery and the build agent inherit the project model
        (`app.project_model`), but the built app had only whatever the agent
        wrote into its own `.env.example` -- observed in the wild as a
        hardcoded `BEDROCK_MODEL_ID=apac.anthropic.claude-sonnet-4-5-...`
        while the project was set to something else entirely.

        Exported to the build step too, for the same reason as base_path: a
        framework may inline `process.env.*` at build time, and a value that
        only appears at start would be baked as undefined.
        """
        if self._launcher is not None:
            # If the wrapper is refusing (aipds/launcher.py header), do not fall back to
            # direct execution.
            # Refuse before stopping what is running — stopping and then failing to start
            # would kill a live link too.
            self._launcher.require()
        # If this (pid, slug) is already running/started, tear down its
        # previous process first.
        await self.stop(pid, slug)

        target_dir = Path(cwd) if cwd is not None else self._root / pid / slug
        # NOT rmtree + re-download: the builder writes into this very
        # directory, so wiping it would delete a live build.
        if not target_dir.is_dir():
            raise FileNotFoundError(str(target_dir))
        if self._launcher is not None:
            # The wrapper computes the tree itself (`<root>/<pid>/<slug>/prototype`). If
            # told to run elsewhere
            # it still runs where it computed, so the mismatch is surfaced here.
            expected = self._root / pid / slug / "prototype"
            if target_dir != expected:
                raise ValueError(f"launcher hosts {expected}, not {target_dir}")
            agent_home.proto(pid, slug).prepare(with_config=False)
        log_path = self._state_dir(pid, slug) / _STATE_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path.touch()

        entry = _HostEntry(dir=target_dir, log_path=log_path, state="installing")
        self._registry[(pid, slug)] = entry

        # Two names for the same value: NEXT_PUBLIC_* is what a Next.js config
        # reads (and the only form inlined into client bundles), PROTO_BASE_PATH
        # is the framework-neutral alias for a Vite/CRA prototype.
        base_env: dict[str, str] = {}
        if base_path:
            base_env = {"NEXT_PUBLIC_BASE_PATH": base_path,
                        "PROTO_BASE_PATH": base_path}
        # Model for the prototype runtime to read. Uses the same name the agent writes
        # into `.env.example` (BEDROCK_MODEL_ID) -- if the names differ, the app
        # will not read it even when injected. No NEXT_PUBLIC_ prefix: the model id is
        # used only for server-side calls, and that prefix inlines the value into the
        # client bundle and ships it to the browser.
        if model_id:
            base_env["BEDROCK_MODEL_ID"] = model_id

        rc = await self._npm_install(pid, slug, target_dir, log_path, base_env)
        if rc != 0:
            entry.state = "failed"
            return self._info(entry)

        pkg = self._read_package_json(target_dir)
        scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}

        # The time **just before** sources are read. Taken later, files changed during the
        # build would be wrongly judged as included (the build never read them); taken
        # before npm install, changes during the minutes the install takes would be wrongly
        # judged stale (the following build does read them). Prototypes without a build
        # script are also captured here — for them, `npm start`/`dev` reads the sources
        # right after.
        entry.built_at = time.time()

        if "build" in scripts:
            entry.state = "building"
            rc = await self._npm_run_build(pid, slug, target_dir, log_path, base_env)
            if rc != 0:
                entry.state = "failed"
                return self._info(entry)

        port = self._scan_port()
        start_script = "start" if "start" in scripts else "dev"
        # `next start` re-reads next.config.js, so the prefix has to be present
        # here too -- otherwise the server would route at "/" while the built
        # assets expect the prefix.
        env = self._env("proto", pid, slug, {**base_env, "PORT": str(port)})

        log_fh = open(log_path, "ab")
        try:
            # The long-lived one, so it is spawned here rather than through
            # `_npm_run_build`. Same reason as there for the literal subcommand: the
            # only variable part of this argv is which script name, and it is
            # one of two names chosen above -- never anything from a request.
            #
            # Own process group (start_new_session): stop() can then signal the
            # whole tree, and a hard backend death leaves a pid file for
            # sweep_orphans instead of an untracked child.
            if self._launcher is not None:
                proc = await self._launcher.exec_npm(
                    "proto", pid, slug, "run", start_script,
                    cwd=str(target_dir), env=env, stdout=log_fh, stderr=log_fh,
                    start_new_session=True,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "npm", "run", start_script, cwd=str(target_dir), env=env,
                    stdout=log_fh, stderr=log_fh, start_new_session=True,
                )
        except Exception:
            # The spawn itself failed (e.g. npm missing from PATH, EMFILE) --
            # entry.port never gets assigned on this path, so stop() (whose
            # release is gated on entry.port is not None) could never reclaim
            # it. Release the reservation here, immediately, instead of
            # leaving it held for the lifetime of this ProtoHost.
            self._reserved.discard(port)
            entry.state = "failed"
            raise
        finally:
            log_fh.close()

        entry.port = port
        entry.proc = proc
        entry.launched = self._launcher is not None
        self._write_state(self._state_dir(pid, slug) / _STATE_PID, str(proc.pid))

        if not await self._wait_for_port(proc, port, timeout=60.0):
            entry.state = "failed"
            return self._info(entry)

        entry.state = "running"
        return self._info(entry)

    async def stop(self, pid: str, slug: str) -> None:
        entry = self._registry.get((pid, slug))
        if entry is None:
            return  # unknown (pid, slug) -- idempotent no-op
        proc = entry.proc
        if proc is not None and proc.returncode is None and entry.launched:
            # The server under the wrapper is not our child (it is a systemd unit).
            # Signaling the client
            # kills only the client and the server stays holding the port (measured) —
            # stopping the
            # unit also ends the client. If it does not end, the path below at least reaps
            # the client.
            await self._launcher.stop("proto", pid, slug)
            try:
                await asyncio.wait_for(proc.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if proc is not None and proc.returncode is None:
            # npm spawns the real server as a child, so signal the GROUP --
            # terminating npm alone orphans the listener and leaks the port.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                await proc.wait()
        if entry.port is not None:
            self._reserved.discard(entry.port)
        (self._state_dir(pid, slug) / _STATE_PID).unlink(missing_ok=True)
        entry.proc = None
        entry.state = "stopped"

    async def purge(self, pid: str, slug: str) -> None:
        """Stop this prototype and delete its local build tree.

        `stop` first, deliberately: removing the directory under a live
        `npm start` would orphan the process, which keeps holding its port (the
        registry entry is what `stop` needs to signal the process group).

        Idempotent -- a tree that was never built, or was already purged, is a
        no-op. `shutil.rmtree` runs in a thread: it is synchronous and a
        node_modules tree is large enough to stall the event loop.

        Raises `RuntimeError` if anything survives the sweep. `ignore_errors`
        lets rmtree get as far as it can instead of aborting on the first bad
        file, so it cannot double as a success signal -- the caller (the
        reset route) needs a raise, not a silently-incomplete no-op, or a
        permission error deep in node_modules would report success while the
        tree (and the "built" card) lives on.

        Raises `ValueError` before touching anything if `pid`/`slug` is not one
        ordinary path segment. The route guards this too (one dependency over
        every prototype endpoint), and that is the primary defence; this is the
        dangerous primitive refusing to be the weapon regardless of who calls
        it. `pathlib` does not normalise, so `self._root / pid / ".."` really
        is root's parent and `"."` really is `self._root / pid` -- an
        unvalidated slug turns one prototype's reset into `rmtree` over every
        project's build tree, or every sibling prototype's.
        """
        reject_unsafe_segment(pid)
        reject_unsafe_segment(slug)
        await self.stop(pid, slug)
        # stop() deliberately KEEPS the registry entry so status() can report
        # "stopped" (test_proto_host.py's test_stop_terminates_process pins
        # that). A purged prototype must not exist at all, so evict it here --
        # otherwise the card would read a state for a tree that is gone.
        self._registry.pop((pid, slug), None)
        # Revoke the access link. `rmtree` below deletes the token FILE, but the
        # in-memory cache would go on resolving that token to this (pid, slug)
        # for the life of the process -- and since the proxy's check is
        # "does the cookie match this prototype's token", a stale cache entry
        # means reset does not actually revoke until the backend restarts.
        # Dropped BEFORE the rmtree so a partial failure cannot leave the token
        # live: the tree surviving is recoverable (retry the reset), a live
        # credential on a prototype the user believes they wiped is not.
        for known, target_ids in list(self._tokens.items()):
            if target_ids == (pid, slug):
                del self._tokens[known]
        # The build agent's transcripts and the hosting npm cache (aipds/agent_home.py). If
        # left, the
        # next build of a reset prototype would see the old session files.
        await agent_home.purge_prototype(pid, slug)
        await asyncio.to_thread(shutil.rmtree, self._state_dir(pid, slug),
                                ignore_errors=True)
        target = self._root / pid / slug
        if not target.is_dir():
            return
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        if target.exists():
            raise RuntimeError(f"purge left residue: {target}")

    async def purge_project(self, pid: str) -> None:
        """Delete this project's prototype root, `{root}/{pid}`. Idempotent.

        `purge` above removes `{root}/{pid}/{slug}`, so purging every slug still
        leaves the parent behind as an empty shell -- observed on the deployed
        instance (2026-08-19): three such directories for projects that no
        longer existed.

        **Callers must run this AFTER purging every slug, never before.** Each
        slug's `purge` calls `stop` first, deliberately: removing a tree under a
        live `npm start` orphans the process, which keeps holding its port. This
        method does not stop anything, so reaching it before the per-slug sweep
        would reproduce exactly that failure.

        Raises `RuntimeError` on residue, and `ValueError` on a `pid` that is not
        one ordinary path segment -- same reasoning as `purge`.
        """
        reject_unsafe_segment(pid)
        await asyncio.to_thread(shutil.rmtree, self._state_root / pid,
                                ignore_errors=True)
        target = self._root / pid
        if not target.is_dir():
            return
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        if target.exists():
            raise RuntimeError(f"project purge left residue: {target}")

    def _pid_files(self) -> list[tuple[Path, str, str]]:
        """(pid file, project_id, slug). Looks at both those in the state tree and those at
        the
        old locations (inside the build tree's `prototype/` and directly under the build
        tree) —
        old-location files were left by a backend predating the move to the state tree, and
        cleanup (`sweep_orphans`) removes them."""
        out = []
        if self._state_root.is_dir():
            for pid_file in self._state_root.glob(f"*/*/{_STATE_PID}"):
                slug_dir = pid_file.parent
                out.append((pid_file, slug_dir.parent.name, slug_dir.name))
        if not self._root.is_dir():
            return out
        for pid_file in self._root.glob("*/*/prototype/.proto-host.pid"):
            slug_dir = pid_file.parent.parent
            out.append((pid_file, slug_dir.parent.name, slug_dir.name))
        for pid_file in self._root.glob("*/*/.proto-host.pid"):
            slug_dir = pid_file.parent
            out.append((pid_file, slug_dir.parent.name, slug_dir.name))
        return out

    def previously_running(self) -> list[tuple[str, str]]:
        """The (project_id, slug) pairs the previous backend was hosting before it went
        away.

        A pid file is written when hosting starts and removed by `stop()` — so a file still
        present after a restart is "hosting that was cut off without ever being told to
        stop".
        It must be read **before** `sweep_orphans` deletes the files.
        """
        return sorted({(pid, slug) for _, pid, slug in self._pid_files()})

    def sweep_orphans(self) -> int:
        """Kill hosting processes left over from a previous backend run and
        clean up their pid files. Replaces the orphan-VM sweep that went away
        with the VM layer: an in-process build's children are OUR children, so
        a hard backend death leaves them holding CPU and ports.

        Best effort -- a pid that no longer exists (or was recycled onto
        something we don't own) only costs a stale file."""
        swept = 0
        if self._launcher is not None:
            # What the pid file pointed to was sudo, and it is already gone. The server
            # remains as a unit.
            self._launcher.sweep()
            for pid_file, _, _ in self._pid_files():
                pid_file.unlink(missing_ok=True)
                swept += 1
            return swept
        for pid_file, _, _ in self._pid_files():
            try:
                target = int(pid_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid_file.unlink(missing_ok=True)
                continue
            try:
                os.killpg(os.getpgid(target), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass  # already gone, or not ours to signal
            pid_file.unlink(missing_ok=True)
            swept += 1
        return swept

    def slugs(self, pid: str) -> list[str]:
        """Every slug this project has **locally**.

        The union of three sources, and all three are needed: disk directories (the build
        tree —
        what survives a restart), the hosting registry (running processes), and the token
        cache (the boundary state where a token was issued but the directory does not exist
        yet).
        This is where project deletion learns what to walk when it cleans up "every slug of
        this project" — enumerating only S3 records would miss trees left only locally
        without a record.

        Read-only, and it does not fail. If a directory cannot be read (permissions, races),
        only that source is emptied — the fact that the list could not be built must be
        judged
        by the caller from other sources, and blocking the whole deletion on an enumeration
        failure is excessive.
        """
        reject_unsafe_segment(pid)
        found = {slug for (p, slug) in self._registry if p == pid}
        found |= {slug for (p, slug) in self._tokens.values() if p == pid}
        for base in (self._root / pid, self._state_root / pid):
            try:
                if base.is_dir():
                    found |= {child.name for child in base.iterdir() if child.is_dir()}
            except OSError:
                # This module has no logger (everything either propagates exceptions or is
                # best-effort).
                # The caller (proto/cleanup.py) records it under its own failure label.
                pass
        return sorted(found)

    def status(self, pid: str, slug: str) -> HostInfo | None:
        """`log_tail` is always an empty string -- this method is the polling path the
        list route calls for each prototype, and only the state is needed (see the comment
        on `_info`). Callers that need the log
        call `log_tail()` separately -- the `/host` route already does.
        """
        entry = self._registry.get((pid, slug))
        if entry is None:
            return None
        if (entry.proc is not None and entry.proc.returncode is not None
                and entry.state == "running"):
            entry.state = "failed"  # crashed after having reached "running"
        return self._info(entry, with_log=False)

    def log_tail(self, pid: str, slug: str, lines: int = 100) -> str:
        entry = self._registry.get((pid, slug))
        if entry is not None:
            return _tail_text(entry.log_path, lines)
        return _tail_text(self._state_dir(pid, slug) / _STATE_LOG, lines)
