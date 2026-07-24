# backend/pathfinder/proto/host.py — ProtoHost: EC2-local prototype hosting.
#
# Downloads a built prototype bundle from S3 (Task 5's PrototypeSession
# uploads it to `prototypes/{slug}/bundle/**`) into a local directory, then
# runs its npm lifecycle (install -> build? -> start) as a real subprocess on
# this host, scanning a free local port for it to listen on. Independent of
# the MicroVM/harness side entirely -- this is the "run the finished thing
# on the backend box" half of the feature (Task 7's routes call it).
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pathfinder.s3store import S3StoreLike

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


def _is_safe_rel(rel: str) -> bool:
    """Reject absolute paths and any `..` segment -- a malicious/corrupt S3
    bundle key must never be able to write outside `root/{pid}/{slug}/`."""
    if not rel or rel.startswith("/"):
        return False
    return ".." not in PurePosixPath(rel).parts


def _scan_port(port_range: range) -> int:
    """First port in `port_range` that a bind succeeds on. Best-effort: the
    port is released immediately after the probe, so a concurrent bind
    between scan and subprocess spawn is possible (accepted per spec) --
    start()'s port-listen poll catches that as a failed start."""
    for port in port_range:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    raise RuntimeError(f"no free port in {port_range}")


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

    def __init__(self, s3, root: Path,
                 port_range: range = range(4001, 4051)):
        # `s3` is either a project-prefixed S3StoreLike (single-project use,
        # as in tests) or a factory `(pid) -> S3StoreLike` — the app-level
        # singleton serves every project, so it hands us the factory and we
        # resolve the per-project store at download time.
        self._s3 = s3
        self._root = Path(root)
        self._port_range = port_range
        self._registry: dict[tuple[str, str], _HostEntry] = {}

    def _store(self, pid: str) -> S3StoreLike:
        return self._s3(pid) if callable(self._s3) else self._s3  # type: ignore[return-value]

    # ---- internals ----

    @staticmethod
    def _append_log(log_path: Path, message: str) -> None:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(message + "\n")

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

    async def _run_npm(self, args: list[str], cwd: Path, log_path: Path) -> int:
        log_fh = open(log_path, "ab")
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", *args, cwd=str(cwd), stdout=log_fh, stderr=log_fh,
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

    async def _download_bundle(self, pid: str, slug: str, target_dir: Path,
                               log_path: Path) -> None:
        s3 = self._store(pid)
        bundle_prefix = f"prototypes/{slug}/bundle/"
        keys = await s3.list(bundle_prefix)
        if not keys:
            raise FileNotFoundError(bundle_prefix)
        for key in keys:
            rel = key[len(bundle_prefix):]
            if not _is_safe_rel(rel):
                self._append_log(log_path, f"skip unsafe bundle key: {key!r}")
                continue
            content = await s3.get(key)
            dest = target_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

    # ---- public interface ----

    async def start(self, pid: str, slug: str) -> HostInfo:
        # If this (pid, slug) is already running/started, tear down its
        # previous process before wiping the directory out from under it.
        await self.stop(pid, slug)

        target_dir = self._root / pid / slug
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        log_path = target_dir / ".proto-host.log"
        log_path.touch()

        # Empty-bundle / missing-slug -> FileNotFoundError propagates (route
        # layer maps this to 404). Raised before any registry entry exists,
        # so status() for this (pid, slug) still reports None afterward.
        await self._download_bundle(pid, slug, target_dir, log_path)

        entry = _HostEntry(dir=target_dir, log_path=log_path, state="installing")
        self._registry[(pid, slug)] = entry

        rc = await self._run_npm(["install"], target_dir, log_path)
        if rc != 0:
            entry.state = "failed"
            return self._info(entry)

        pkg = self._read_package_json(target_dir)
        scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}

        if "build" in scripts:
            entry.state = "building"
            rc = await self._run_npm(["run", "build"], target_dir, log_path)
            if rc != 0:
                entry.state = "failed"
                return self._info(entry)

        port = _scan_port(self._port_range)
        start_args = ["run", "start"] if "start" in scripts else ["run", "dev"]
        env = {**os.environ, "PORT": str(port)}

        log_fh = open(log_path, "ab")
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", *start_args, cwd=str(target_dir), env=env,
                stdout=log_fh, stderr=log_fh,
            )
        finally:
            log_fh.close()

        entry.port = port
        entry.proc = proc

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
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        entry.proc = None
        entry.state = "stopped"

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
