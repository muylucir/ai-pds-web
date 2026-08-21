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
from typing import Literal

from aipds.pathsafe import reject_unsafe_segment

HostState = Literal["installing", "building", "running", "failed", "stopped"]

#: The access token file, in the build directory's PARENT -- a sibling of
#: `.proto-host.pid`/`.proto-host.log`, NOT inside the served `prototype/` tree.
#:
#: That placement is load-bearing twice over. The served tree is what a
#: prototype's own dev server can hand out, and it is also what
#: `_archive_entries` zips for the "download" button -- a token under
#: `prototype/` would ride out to whoever downloads the bundle, which is
#: exactly the audience the token exists to gate.
TOKEN_FILENAME = ".proto-token"

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


@dataclass
class _HostEntry:
    """Internal registry record -- not part of the public interface."""
    dir: Path
    log_path: Path
    state: HostState
    port: int | None = None
    proc: "asyncio.subprocess.Process | None" = None


def _tail_text(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace")
    all_lines = content.splitlines()
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
    def __init__(self, root: Path, port_range: range = range(4000, 8000)):
        # No `s3`: the build directory IS the served tree now (the builder
        # writes straight into it), so hosting no longer round-trips a bundle
        # through S3 -- which also means binary assets stop being mangled by
        # the text-only store.
        self._root = Path(root)
        self._port_range = port_range
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

    def _token_path(self, pid: str, slug: str) -> Path:
        reject_unsafe_segment(pid)
        reject_unsafe_segment(slug)
        return self._root / pid / slug / TOKEN_FILENAME

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
        if not self._root.is_dir():
            return 0
        for token_file in self._root.glob(f"*/*/{TOKEN_FILENAME}"):
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        self._tokens[token] = (pid, slug)
        return token

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
        """`with_log=False` carries only the state, without reading the log.

        Because `_tail_text` reads the **whole** file (`read_text()`) to get the last 100
        lines, and `.proto-host.log` grows by append only with no rotation ("ab" in
        `_run_npm`). Repeated hosting keeps piling up `npm install` and `npm run build`
        output. The measured cost per call: 1MB -> 1.9ms, 20MB -> 46ms, 100MB -> 237ms.

        That read is **synchronous I/O** inside an async function, so it holds the event loop
        outright -- every refresh of the list stalls every SSE stream in flight for that
        long. The list route calls `status()` once per prototype, so it multiplies by their
        count. That is why `status()` does not read the log.

        The two places that genuinely need the log are left alone: `start()`'s failure
        diagnosis (which goes out as a 502 detail) and `log_tail()` (the moment the user
        presses "view log"). Both are called infrequently and the user is waiting for the
        content.
        """
        return HostInfo(
            state=entry.state, port=entry.port,
            log_tail=_tail_text(entry.log_path, 100) if with_log else "")

    @staticmethod
    def _read_package_json(target_dir: Path) -> dict:
        try:
            return json.loads((target_dir / "package.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    async def _run_npm(self, args: list[str], cwd: Path, log_path: Path,
                       env: dict[str, str] | None = None) -> int:
        log_fh = open(log_path, "ab")
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", *args, cwd=str(cwd), stdout=log_fh, stderr=log_fh,
                env=env,
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
        # If this (pid, slug) is already running/started, tear down its
        # previous process first.
        await self.stop(pid, slug)

        target_dir = Path(cwd) if cwd is not None else self._root / pid / slug
        # NOT rmtree + re-download: the builder writes into this very
        # directory, so wiping it would delete a live build.
        if not target_dir.is_dir():
            raise FileNotFoundError(str(target_dir))
        log_path = target_dir / ".proto-host.log"
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
        # The model the prototype runtime will read. It uses the same name the agent writes
        # into `.env.example` (BEDROCK_MODEL_ID) -- with a mismatched name, injecting it
        # would not make the app read it. No NEXT_PUBLIC_ prefix: the model id is used only
        # in server-side calls, and that prefix would inline the value into the client
        # bundle and ship it to the browser.
        if model_id:
            base_env["BEDROCK_MODEL_ID"] = model_id

        rc = await self._run_npm(["install"], target_dir, log_path,
                                 env={**os.environ, **base_env})
        if rc != 0:
            entry.state = "failed"
            return self._info(entry)

        pkg = self._read_package_json(target_dir)
        scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}

        if "build" in scripts:
            entry.state = "building"
            rc = await self._run_npm(["run", "build"], target_dir, log_path,
                                     env={**os.environ, **base_env})
            if rc != 0:
                entry.state = "failed"
                return self._info(entry)

        port = self._scan_port()
        start_args = ["run", "start"] if "start" in scripts else ["run", "dev"]
        # `next start` re-reads next.config.js, so the prefix has to be present
        # here too -- otherwise the server would route at "/" while the built
        # assets expect the prefix.
        env = {**os.environ, **base_env, "PORT": str(port)}

        log_fh = open(log_path, "ab")
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", *start_args, cwd=str(target_dir), env=env,
                stdout=log_fh, stderr=log_fh,
                # Own process group: stop() can then signal the whole tree,
                # and a hard backend death leaves a pid file for sweep_orphans
                # instead of an untracked child.
                start_new_session=True,
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
        (target_dir / ".proto-host.pid").write_text(str(proc.pid), encoding="utf-8")

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
        (entry.dir / ".proto-host.pid").unlink(missing_ok=True)
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
        target = self._root / pid
        if not target.is_dir():
            return
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        if target.exists():
            raise RuntimeError(f"project purge left residue: {target}")

    def sweep_orphans(self) -> int:
        """Kill hosting processes left over from a previous backend run and
        clean up their pid files. Replaces the orphan-VM sweep that went away
        with the VM layer: an in-process build's children are OUR children, so
        a hard backend death leaves them holding CPU and ports.

        Best effort -- a pid that no longer exists (or was recycled onto
        something we don't own) only costs a stale file."""
        swept = 0
        if not self._root.is_dir():
            return 0
        for pid_file in self._root.glob("*/*/.proto-host.pid"):
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
        """Every slug this project holds **locally**.

        The union of three sources, all three of which are needed: disk directories (build
        trees -- what survives a restart), the hosting registry (running processes), and the
        token cache (the boundary state where a token has been issued but the directory does
        not exist yet). What a project deletion has to walk when cleaning up "every slug in
        this project" comes from here -- enumerating only the S3 record would miss a tree
        that remains locally with no record.

        Read-only and it does not fail. If a directory cannot be read (permissions,
        contention) only that source comes back empty -- the fact that the listing could not
        be built is for the caller to judge from the other sources, and blocking an entire
        deletion over an enumeration failure would be excessive.
        """
        reject_unsafe_segment(pid)
        found = {slug for (p, slug) in self._registry if p == pid}
        found |= {slug for (p, slug) in self._tokens.values() if p == pid}
        base = self._root / pid
        try:
            if base.is_dir():
                found |= {child.name for child in base.iterdir() if child.is_dir()}
        except OSError:
            # This module keeps no logger (everything either propagates an exception or is
            # best-effort). The caller (proto/cleanup.py) records it under its own failure
            # label.
            pass
        return sorted(found)

    def status(self, pid: str, slug: str) -> HostInfo | None:
        """`log_tail` is always an empty string -- this method is the polling path the list
        route calls once per prototype, and reading the log stalls the event loop (the
        measurements are in `_info`'s comment). A caller that needs the log calls
        `log_tail()` separately -- the `/host` route already does.
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
        return _tail_text(self._root / pid / slug / ".proto-host.log", lines)
