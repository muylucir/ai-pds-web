# backend/pathfinder/proto/host.py — ProtoHost: EC2-local prototype hosting.
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
import signal
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

HostState = Literal["installing", "building", "running", "failed", "stopped"]


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
    # PATHFINDER_PROTO_MAX_CONCURRENT caps live builds at 2 -- `_scan_port`
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

    # ---- internals ----

    @staticmethod
    def _info(entry: _HostEntry) -> HostInfo:
        return HostInfo(state=entry.state, port=entry.port,
                         log_tail=_tail_text(entry.log_path, 100))

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
                    base_path: str | None = None) -> HostInfo:
        """Run the npm lifecycle for one prototype and return its HostInfo.

        `base_path` is the public prefix the reverse proxy serves this prototype
        under (`/proto/{pid}/{slug}`). It is exported to BOTH the build and the
        start step, because Next.js bakes `basePath` into its output at build
        time -- injecting it only at start, the way PORT is, would come too late
        and leave the asset URLs pointing at the CloudFront root. Left unset,
        the env vars are absent rather than empty, so a config reading
        `process.env.NEXT_PUBLIC_BASE_PATH ?? ''` can still tell "no prefix"
        from "prefix I forgot to pass".
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

    def status(self, pid: str, slug: str) -> HostInfo | None:
        entry = self._registry.get((pid, slug))
        if entry is None:
            return None
        if (entry.proc is not None and entry.proc.returncode is not None
                and entry.state == "running"):
            entry.state = "failed"  # crashed after having reached "running"
        return self._info(entry)

    def log_tail(self, pid: str, slug: str, lines: int = 100) -> str:
        entry = self._registry.get((pid, slug))
        if entry is not None:
            return _tail_text(entry.log_path, lines)
        return _tail_text(self._root / pid / slug / ".proto-host.log", lines)
