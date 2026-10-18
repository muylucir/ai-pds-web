# backend/aipds/routes/deps.py
import asyncio
import logging
from fastapi import HTTPException
from aipds import app as app_module
from aipds.workspace import Workspace

_log = logging.getLogger(__name__)
# Per-pid boot lock — keeps two concurrent requests right after restore from starting the VM twice.
_boot_locks: dict[str, asyncio.Lock] = {}


async def ensure_workspace(pid: str) -> Workspace:
    """Return the live workspace; if the project is only restored-and-registered, lazily
    initialize its workspace (local directory) right here (spec: restore time = first access).
    Unregistered → 404; init failure → 503 (registration is kept — the next request retries)."""
    try:
        return app_module.registry.get(pid)
    except KeyError:
        pass
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    lock = _boot_locks.setdefault(pid, asyncio.Lock())
    async with lock:
        try:
            return app_module.registry.get(pid)  # double-check: an earlier request already initialized it
        except KeyError:
            pass
        try:
            workspace = await app_module.make_workspace(pid)
        except Exception:
            _log.exception("lazy workspace init failed for %s", pid)
            raise HTTPException(status_code=503, detail="project workspace unavailable")
        try:
            return app_module.registry.attach(pid, workspace)
        except KeyError:
            # DELETE /projects/{pid} cut in while waiting for init: the project is already
            # unregistered. Stop the just-created workspace's runner best-effort so it doesn't leak,
            # and return 404, matching the usual unregistered-project semantics.
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("failed to stop runner for deleted-during-boot project %s", pid)
            raise HTTPException(status_code=404, detail="unknown project")
