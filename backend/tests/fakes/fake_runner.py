# backend/tests/fakes/fake_runner.py
from __future__ import annotations
from aipds.globmatch import matches_glob
from aipds.pathsafe import reject_unsafe
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
        reject_unsafe(rel)
        return await self._s3.get(rel)

    async def write_file(self, rel: str, content: str) -> None:
        reject_unsafe(rel)
        await self._s3.put(rel, content)

    async def write_file_if_absent(self, rel: str, content: str) -> bool:
        reject_unsafe(rel)
        return await self._s3.put_if_absent(rel, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list("")
        return sorted(k for k in keys if matches_glob(k, glob))

    async def list_files_newest_first(self, glob: str) -> list[str]:
        # 실물과 같은 계약이다(aipds/runner.py) — 페이크가 이 메서드를 빠뜨리면
        # `Workspace.list_artifacts`가 테스트에서만 AttributeError로 죽는다.
        reject_unsafe(glob)
        pairs = await self._s3.list_with_times("")
        matched = [(k, t) for k, t in pairs if matches_glob(k, glob)]
        matched.sort(key=lambda item: (-item[1], item[0]))
        return [k for k, _ in matched]

    def set_input_holder(self, holder):
        self.input_holder = holder

    async def stop(self) -> None:
        pass
