from __future__ import annotations

class FakeS3Store:
    """In-memory S3StoreLike for MicroVMSandbox unit tests (no boto3, no AWS).

    Same contract as S3Store: text in/out, get() raises FileNotFoundError on a
    missing key, list(prefix) returns sorted workspace-relative keys.
    """

    def __init__(self) -> None:
        self.blobs: dict[str, str] = {}

    async def get(self, key: str) -> str:
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]

    async def put(self, key: str, content: str) -> None:
        self.blobs[key] = content

    async def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self.blobs if k.startswith(prefix))

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self.blobs if k.startswith(prefix)]
        for k in doomed:
            del self.blobs[k]
        return len(doomed)
