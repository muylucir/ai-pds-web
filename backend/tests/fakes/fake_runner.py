# backend/tests/fakes/fake_runner.py
from __future__ import annotations
from aipds.globmatch import matches_glob
from aipds.pathsafe import reject_unsafe
from aipds.models import AgentEvent
from fakes.in_memory_s3 import FakeS3Store


class FakeRunner:
    """File-backed test double for AgentRunner: the file-as-contract ops
    (read_file/write_file/list_files) resolve against an in-memory S3 store,
    exactly like the real runner's durable-S3-direct ops — no AWS, no boot.
    stop() is a no-op."""

    def __init__(self, s3: FakeS3Store | None = None):
        self._s3 = s3 or FakeS3Store()
        #: Text of turns started with `send_message` — where tests read the sentences the route
        #: sent to the agent.
        self.sent: list[str] = []
        #: Record of user input the route passed in (aipds/audit_log.UserInput) — where tests read
        #: what will remain in the audit log.
        self.records: list = []

    def send_message(self, text: str, record=None):
        """One turn: record the text and finish immediately.

        Why this is a function that **returns** a generator rather than an async generator function: the turn
        job (turn_job.py) calls it within the request, and a task consumes it. Recording at call time
        makes it readable regardless of when the task runs.
        """
        self.sent.append(text)
        self.records.append(record)

        async def events():
            yield AgentEvent(kind="done")
        return events()

    def send_answers(self, answers: dict, record=None):
        """SDK question-answer turn. Returns a generator for the same reason as `send_message`."""
        self.records.append(record)

        async def events():
            yield AgentEvent(kind="done")
        return events()

    async def read_file(self, rel: str) -> str:
        reject_unsafe(rel)
        return await self._s3.get(rel)

    async def write_file(self, rel: str, content: str) -> None:
        reject_unsafe(rel)
        await self._s3.put(rel, content)

    async def write_file_if_absent(self, rel: str, content: str) -> bool:
        reject_unsafe(rel)
        return await self._s3.put_if_absent(rel, content)

    async def write_bytes_if_absent(self, rel: str, content: bytes) -> bool:
        reject_unsafe(rel)
        return await self._s3.put_bytes_if_absent(rel, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list("")
        return sorted(k for k in keys if matches_glob(k, glob))

    async def list_files_newest_first(self, glob: str) -> list[str]:
        # The same contract as the real one (aipds/runner.py) — if the fake left out this method,
        # `Workspace.list_artifacts` would die with an AttributeError only in tests.
        reject_unsafe(glob)
        pairs = await self._s3.list_with_times("")
        matched = [(k, t) for k, t in pairs if matches_glob(k, glob)]
        matched.sort(key=lambda item: (-item[1], item[0]))
        return [k for k, _ in matched]

    async def stop(self) -> None:
        pass
