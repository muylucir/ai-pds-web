# backend/tests/fakes/fake_runner.py
from __future__ import annotations
from pathfinder.globmatch import matches_glob
from fakes.in_memory_s3 import FakeS3Store


class FakeRunner:
    """File-backed test double for AgentRunner: the file-as-contract ops
    (read_file/write_file/list_files) resolve against an in-memory S3 store,
    exactly like the real runner's durable-S3-direct ops — no AWS, no boot.
    stop() is a no-op; input_holder mirrors the real attribute."""

    def __init__(self, s3: FakeS3Store | None = None):
        self._s3 = s3 or FakeS3Store()
        self.input_holder = None

    async def read_file(self, rel: str) -> str:
        return await self._s3.get(rel)

    async def write_file(self, rel: str, content: str) -> None:
        await self._s3.put(rel, content)

    async def list_files(self, glob: str) -> list[str]:
        keys = await self._s3.list("")
        return sorted(k for k in keys if matches_glob(k, glob))

    def set_input_holder(self, holder):
        self.input_holder = holder

    async def stop(self) -> None:
        pass
