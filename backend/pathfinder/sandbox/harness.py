# backend/pathfinder/sandbox/harness.py
from __future__ import annotations
import json
from typing import AsyncIterator
import httpx
from pathfinder.sandbox.base import AgentEvent

_TERMINAL = ("done", "error")

class HarnessClient:
    """HTTP client for the MicroVM harness protocol (spec §2).

    Pure transport: performs no path-safety (the caller guarantees safe paths)
    and no credential redaction (that happens at the route seam, on the
    AgentEvent objects this yields). `http` is injected so tests can drive a
    fake ASGI harness via httpx.ASGITransport.
    """

    def __init__(self, base_url: str, http: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._http = http

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        async with self._http.stream(
            "POST", f"{self._base}/message", json={"text": text}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                event = AgentEvent(**json.loads(payload))
                yield event
                if event.kind in _TERMINAL:
                    return

    async def read_file(self, rel_path: str) -> str:
        resp = await self._http.get(f"{self._base}/files/{rel_path}")
        if resp.status_code == 404:
            raise FileNotFoundError(rel_path)
        resp.raise_for_status()
        return resp.text

    async def write_file(self, rel_path: str, content: str) -> None:
        resp = await self._http.put(
            f"{self._base}/files/{rel_path}",
            content=content.encode("utf-8"),
        )
        resp.raise_for_status()

    async def list_files(self, glob: str) -> list[str]:
        resp = await self._http.get(f"{self._base}/files", params={"glob": glob})
        resp.raise_for_status()
        return list(resp.json())

    async def heartbeat(self) -> bool:
        try:
            resp = await self._http.get(f"{self._base}/health")
        except httpx.HTTPError:
            return False
        return resp.is_success
