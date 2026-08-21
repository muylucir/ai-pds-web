from __future__ import annotations
import hashlib

class FakeS3Store:
    """In-memory S3StoreLike for runner/route unit tests (no boto3, no AWS).

    Stores bytes internally so the text and binary APIs share one namespace,
    exactly as S3Store does -- a text put must be visible to get_bytes and
    vice versa.
    """

    def __init__(self) -> None:
        self._raw: dict[str, bytes] = {}
        # 키 -> 쓰기 순번. `list_with_times`의 "시각"이다. 벽시계를 쓰지 않는
        # 이유: 같은 테스트 안의 연속 쓰기가 같은 밀리초에 들어가 순서가 흔들린다.
        self._seq: dict[str, int] = {}
        self._tick = 0

    # `blobs` stays the text-facing view the existing tests were written
    # against: `s3.blobs[key] = "..."` and `assert s3.blobs[key] == "..."`.
    @property
    def blobs(self) -> "_TextView":
        return _TextView(self._raw)

    async def get(self, key: str) -> str:
        return (await self.get_bytes(key)).decode("utf-8")

    async def put(self, key: str, content: str) -> str:
        await self.put_bytes(key, content.encode("utf-8"))
        return self._etag(self._raw[key])

    async def put_if_absent(self, key: str, content: str) -> bool:
        if key in self._raw:
            return False
        await self.put(key, content)
        return True

    async def get_bytes(self, key: str) -> bytes:
        if key not in self._raw:
            raise FileNotFoundError(key)
        return self._raw[key]

    async def put_bytes(self, key: str, content: bytes) -> None:
        self._raw[key] = content
        self._tick += 1
        self._seq[key] = self._tick

    async def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self._raw if k.startswith(prefix))

    async def list_with_times(self, prefix: str) -> list[tuple[str, float]]:
        return [(k, float(self._seq.get(k, 0)))
                for k in sorted(self._raw) if k.startswith(prefix)]

    async def list_with_etags(self, prefix: str) -> list[tuple[str, str]]:
        return [(k, self._etag(self._raw[k])) for k in await self.list(prefix)]

    @staticmethod
    def _etag(content: bytes) -> str:
        return f'"{hashlib.md5(content, usedforsecurity=False).hexdigest()}"'

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._raw if k.startswith(prefix)]
        for k in doomed:
            del self._raw[k]
        return len(doomed)


class _TextView:
    """dict-like text view over the byte store, so existing tests that do
    `s3.blobs[key] = "text"` / `key in s3.blobs` keep working unchanged."""

    def __init__(self, raw: dict[str, bytes]):
        self._raw = raw

    def __setitem__(self, key: str, value: str) -> None:
        self._raw[key] = value.encode("utf-8")

    def __getitem__(self, key: str) -> str:
        return self._raw[key].decode("utf-8")

    def __delitem__(self, key: str) -> None:
        del self._raw[key]

    def __contains__(self, key: object) -> bool:
        return key in self._raw

    def __iter__(self):
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _TextView):
            return self._raw == other._raw
        return NotImplemented

    def get(self, key: str, default=None):
        raw = self._raw.get(key)
        return default if raw is None else raw.decode("utf-8")

    def update(self, other: dict) -> None:
        for k, v in other.items():
            self[k] = v

    def keys(self):
        return self._raw.keys()

    def values(self):
        return [v.decode("utf-8") for v in self._raw.values()]

    def items(self):
        return [(k, v.decode("utf-8")) for k, v in self._raw.items()]
