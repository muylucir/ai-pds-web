# backend/pathfinder/proto/harness_client.py
from __future__ import annotations
import json
from typing import AsyncIterator
import httpx
from pathfinder.models import AgentEvent

_TERMINAL = ("done", "error")


class HarnessClient:
    """HTTP client for the MicroVM harness protocol.

    Pure transport: performs no path-safety (the caller guarantees safe paths)
    and no credential redaction (that happens at the route seam, on the
    AgentEvent objects this yields). `http` is injected so tests can drive a
    fake ASGI harness via httpx.ASGITransport (or a MockTransport).

    No `session` param: the SDK driver running inside the harness keeps
    conversation context in-process, so no session descriptor crosses the
    wire (unlike the historical CLI-subprocess harness).
    """

    def __init__(
        self,
        base_url: str,
        http: httpx.AsyncClient,
        headers: dict[str, str] | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._http = http
        # Per-handle auth (e.g. X-aws-proxy-auth JWE), merged into every
        # request. app.py keeps ONE shared AsyncClient; auth is attached
        # per HarnessClient, not on the shared client.
        self._headers = headers or None

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        async with self._http.stream(
            "POST", f"{self._base}/message", json={"text": text},
            headers=self._headers,
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

    async def send_answers(self, interrupt_id: str,
                            answers: dict[str, str]) -> bool:
        r = await self._http.post(
            f"{self._base}/answers",
            json={"interrupt_id": interrupt_id, "answers": answers},
            headers=self._headers,
        )
        if r.status_code == 409:
            return False
        r.raise_for_status()
        return True

    async def interrupt(self) -> None:
        r = await self._http.post(f"{self._base}/interrupt", headers=self._headers)
        r.raise_for_status()

    async def pending(self) -> str | None:
        resp = await self._http.post(
            f"{self._base}/pending", json={},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()["pending"]

    async def read_file(self, rel_path: str) -> str:
        resp = await self._http.get(
            f"{self._base}/files/{rel_path}", headers=self._headers
        )
        if resp.status_code == 404:
            raise FileNotFoundError(rel_path)
        resp.raise_for_status()
        return resp.text

    async def write_file(self, rel_path: str, content: str) -> None:
        resp = await self._http.put(
            f"{self._base}/files/{rel_path}",
            content=content.encode("utf-8"),
            headers=self._headers,
        )
        resp.raise_for_status()

    async def list_files(self, glob: str) -> list[str]:
        resp = await self._http.get(
            f"{self._base}/files", params={"glob": glob}, headers=self._headers
        )
        resp.raise_for_status()
        return list(resp.json())

    async def heartbeat(self) -> bool:
        try:
            resp = await self._http.get(
                f"{self._base}/health", headers=self._headers
            )
        except httpx.HTTPError:
            return False
        return resp.is_success
