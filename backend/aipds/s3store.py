from __future__ import annotations
import asyncio
from collections import OrderedDict
from typing import Protocol

from botocore.exceptions import ClientError


class S3StoreLike(Protocol):
    async def get(self, key: str) -> str: ...
    async def put(self, key: str, content: str) -> str | None: ...
    async def put_if_absent(self, key: str, content: str) -> bool: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def list_with_etags(self, prefix: str) -> list[tuple[str, str]]: ...
    async def list_with_times(self, prefix: str) -> list[tuple[str, float]]: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    # Binary-safe methods, for what is not markdown: the prototype bundle
    # backup/restore, the handoff zip, project export/import and uploaded
    # originals (images, PDFs). The text methods above decode as UTF-8, which
    # mangles images and fonts (U+FFFD).
    async def get_bytes(self, key: str) -> bytes: ...
    async def put_bytes(self, key: str, content: bytes) -> None: ...
    async def put_bytes_if_absent(self, key: str, content: bytes) -> bool: ...


class _BodyCache:
    """(bucket, full key, ETag) → body. Over the byte budget, the least recently used entries are evicted.

    An ETag identifies the object's content -- writing the same key again changes the
    ETag, so a body found under this key can never be wrong after an overwrite,
    deletion, or import (a wrong one simply does not match). So no invalidation is
    needed.

    Touched only on the event loop thread -- `get_cached` reads and writes it outside
    the worker thread.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max = max_bytes
        self._size = 0
        self._items: OrderedDict[tuple[str, str, str], str] = OrderedDict()

    def get(self, key: tuple[str, str, str]) -> str | None:
        body = self._items.get(key)
        if body is not None:
            self._items.move_to_end(key)
        return body

    def put(self, key: tuple[str, str, str], body: str) -> None:
        cost = len(body)
        if cost > self._max or key in self._items:
            return
        self._items[key] = body
        self._size += cost
        while self._size > self._max:
            _, dropped = self._items.popitem(last=False)
            self._size -= len(dropped)


#: For the transcript batches and answer records that history restore opens. Instead of
#: re-GETting hundreds of them every time a workspace opens, only changed ones (= a
#: different ETag) are fetched. The budget is measured in characters, and overflowing
#: it only means the least recently opened projects get fetched again.
_body_cache = _BodyCache(64 * 1024 * 1024)


class S3Store:
    """Durable blob store over S3 (Seoul, ap-northeast-2). Thin: text in/out,
    workspace-relative keys namespaced under `prefix`. Path-safety and key
    composition are the caller's (AgentRunner) job. boto3 is synchronous, so
    each call is wrapped in asyncio.to_thread to keep the async surface without
    an async AWS SDK. Auth is the host IAM role — no keys are held here.
    """

    def __init__(self, bucket: str, prefix: str, client) -> None:
        self._bucket = bucket
        self._prefix = prefix if prefix.endswith("/") or prefix == "" else prefix + "/"
        self._client = client

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> str:
        def _get() -> str:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=self._full_key(key))
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise FileNotFoundError(key) from e
                raise
            return resp["Body"].read().decode("utf-8")

        return await asyncio.to_thread(_get)

    async def get_cached(self, key: str, etag: str) -> str:
        """Like `get`, but skips S3 if the body for the ETag the listing reported was already fetched.

        A fetched body is stored under **the GET response's ETag** -- if the object
        changed between the listing and the GET, the listing's ETag is already stale,
        and storing the new body under that name would make the cache lie.
        """
        full = self._full_key(key)
        hit = _body_cache.get((self._bucket, full, etag))
        if hit is not None:
            return hit

        def _get() -> tuple[str, str]:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=full)
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise FileNotFoundError(key) from e
                raise
            return resp["Body"].read().decode("utf-8"), resp.get("ETag", "")

        body, actual = await asyncio.to_thread(_get)
        if actual:
            _body_cache.put((self._bucket, full, actual), body)
        return body

    async def put(self, key: str, content: str) -> str | None:
        def _put() -> str | None:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=self._full_key(key),
                Body=content.encode("utf-8"),
            )
            return response.get("ETag")

        return await asyncio.to_thread(_put)

    async def put_if_absent(self, key: str, content: str) -> bool:
        """Conditional write (S3 IfNoneMatch). Returns False if the key
        already exists instead of replacing it. Used by the upload path as a
        backstop behind its uuid keys -- a silent overwrite there costs a
        user's file."""
        return await self.put_bytes_if_absent(key, content.encode("utf-8"))

    async def put_bytes_if_absent(self, key: str, content: bytes) -> bool:
        """`put_if_absent` for an upload's original (an image, a PDF)."""
        def _put() -> bool:
            try:
                self._client.put_object(
                    Bucket=self._bucket, Key=self._full_key(key),
                    Body=content, IfNoneMatch="*")
            except ClientError as e:
                if e.response["Error"]["Code"] in ("PreconditionFailed", "412"):
                    return False
                raise
            return True

        return await asyncio.to_thread(_put)

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=self._full_key(key))
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise FileNotFoundError(key) from e
                raise
            return resp["Body"].read()

        return await asyncio.to_thread(_get)

    async def put_bytes(self, key: str, content: bytes) -> None:
        def _put() -> None:
            self._client.put_object(Bucket=self._bucket,
                                    Key=self._full_key(key), Body=content)

        await asyncio.to_thread(_put)

    async def list(self, prefix: str) -> list[str]:
        return [key for key, _ in await self.list_with_etags(prefix)]

    async def list_with_etags(self, prefix: str) -> list[tuple[str, str]]:
        return [(key, etag) for key, etag, _ in await self._list_meta(prefix)]

    async def list_with_times(self, prefix: str) -> list[tuple[str, float]]:
        """Keys and last-modified times (epoch seconds). Used to sort listings newest first.

        `list_objects_v2` already includes `LastModified` in its response -- previously
        that was discarded, so `Workspace.list_artifacts` could only be alphabetical.
        There is no extra call, so the cost is zero.
        """
        return [(key, mtime) for key, _, mtime in await self._list_meta(prefix)]

    async def _list_meta(self, prefix: str) -> list[tuple[str, str, float]]:
        def _list() -> list[tuple[str, str, float]]:
            full = self._full_key(prefix)
            paginator = self._client.get_paginator("list_objects_v2")
            keys: list[tuple[str, str, float]] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=full):
                for obj in page.get("Contents", []):
                    last = obj.get("LastModified")
                    keys.append((
                        obj["Key"][len(self._prefix):],
                        obj.get("ETag", ""),
                        last.timestamp() if last is not None else 0.0,
                    ))
            # Return a stable sort by key -- callers that need chronological order sort
            # again, and the order of entries with the same time does not shift between
            # runs.
            return sorted(keys, key=lambda item: item[0])

        return await asyncio.to_thread(_list)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under a relative prefix within the namespace (batches of 1000).

        Only for the project deletion path -- list then delete_objects is not atomic,
        but deletion is idempotent, so a partial failure converges on a retry."""
        def _delete() -> int:
            full = self._full_key(prefix)
            paginator = self._client.get_paginator("list_objects_v2")
            keys = [obj["Key"]
                    for page in paginator.paginate(Bucket=self._bucket, Prefix=full)
                    for obj in page.get("Contents", [])]
            errors: list[dict] = []
            for i in range(0, len(keys), 1000):  # S3 delete_objects limit
                resp = self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                            "Quiet": True})
                errors.extend(resp.get("Errors", []))
            if errors:
                raise RuntimeError(
                    f"delete_prefix: {len(errors)}/{len(keys)} objects failed "
                    f"(first: {errors[0].get('Key')}: {errors[0].get('Code')})")
            return len(keys)

        return await asyncio.to_thread(_delete)


# ---- Helpers that use optional protocol features ----
#
# Tests and small adapters sometimes implement only `list`/`get` (`AgentRunner._list_with_etags`
# has the same fallback for the same reason). Without ETags it GETs every time -- slow, but not wrong.

async def list_with_etags(store: S3StoreLike,
                          prefix: str) -> list[tuple[str, str | None]]:
    listing = getattr(store, "list_with_etags", None)
    if listing is not None:
        return await listing(prefix)
    return [(key, None) for key in await store.list(prefix)]


async def get_by_etag(store: S3StoreLike, key: str, etag: str | None) -> str:
    cached = getattr(store, "get_cached", None)
    if cached is None or not etag:
        return await store.get(key)
    return await cached(key, etag)
