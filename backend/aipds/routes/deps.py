# backend/aipds/routes/deps.py
import asyncio
import logging
from fastapi import HTTPException
from aipds import app as app_module
from aipds.workspace import Workspace

_log = logging.getLogger(__name__)
# Per-pid boot lock -- stops two concurrent requests right after a restore from
# bringing the VM up twice.
_boot_locks: dict[str, asyncio.Lock] = {}


async def ensure_workspace(pid: str) -> Workspace:
    """Return the live workspace, lazily initialising it (the local directory) here
    if the project is registered-from-restore only (per spec: restore happens on
    first access). Unregistered is 404; a failed initialisation is 503 and leaves
    the registration in place so the next request retries."""
    try:
        return app_module.registry.get(pid)
    except KeyError:
        pass
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    lock = _boot_locks.setdefault(pid, asyncio.Lock())
    async with lock:
        try:
            # double-check: an earlier request already initialised it
            return app_module.registry.get(pid)
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
            # DELETE /projects/{pid} slipped in while we were waiting to
            # initialise: the project is already unregistered. Stop the runner of
            # the workspace we just built (best-effort, so it does not leak) and
            # return the usual 404 for an unknown project.
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("failed to stop runner for deleted-during-boot project %s", pid)
            raise HTTPException(status_code=404, detail="unknown project")
