# backend/tests/test_access_log.py — identity logging for authenticated requests (aipds/access_log.py).
from __future__ import annotations

import json
import logging

from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.access_log import AccessLogMiddleware
from aipds.auth.deps import require_user


def _probe() -> TestClient:
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.post("/projects/{pid}/things", dependencies=[Depends(require_user)])
    async def change(pid: str):
        return {"ok": True}

    @app.get("/projects/{pid}", dependencies=[Depends(require_user)])
    async def read(pid: str):
        return {"ok": True}

    @app.get("/projects/{pid}/export", dependencies=[Depends(require_user)])
    async def export(pid: str):
        return PlainTextResponse("zip", headers={
            "Content-Disposition": 'attachment; filename="p.zip"'})

    @app.post("/public/answer")
    async def public():
        return {"ok": True}

    return TestClient(app)


def _records(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records
            if r.name == "aipds.access"]


def test_state_changes_and_downloads_are_recorded_with_the_requester(caplog, monkeypatch):
    monkeypatch.setattr(app_module, "cognito_config", lambda: None)
    caplog.set_level(logging.INFO, logger="aipds.access")
    client = _probe()

    client.post("/projects/p1/things?t=query-value")
    client.get("/projects/p1")                 # polling: not logged
    client.get("/projects/p1/export")          # attachment: logged
    client.post("/public/answer")              # no identity: not logged

    records = _records(caplog)
    assert [(r["method"], r["path"]) for r in records] == [
        ("POST", "/projects/p1/things"), ("GET", "/projects/p1/export")]
    assert records[0]["user"] == "local-dev" and records[0]["status"] == 200
    assert all("query-value" not in json.dumps(r) for r in records)
