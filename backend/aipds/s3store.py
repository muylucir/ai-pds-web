from __future__ import annotations
import asyncio
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
    # Binary-safe pair, used only by the prototype bundle backup/restore and
    # the handoff zip. The text methods above decode as UTF-8, which mangles
    # images and fonts (U+FFFD) -- fine for markdown, wrong for a bundle.
    async def get_bytes(self, key: str) -> bytes: ...
    async def put_bytes(self, key: str, content: bytes) -> None: ...


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
        def _put() -> bool:
            try:
                self._client.put_object(
                    Bucket=self._bucket, Key=self._full_key(key),
                    Body=content.encode("utf-8"), IfNoneMatch="*")
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
        """The keys with their last-modified times (epoch seconds). Used to sort a listing
        newest-first.

        `list_objects_v2` already carries `LastModified` in its response -- that used to be
        discarded, which is why `Workspace.list_artifacts` could only be alphabetical. There is
        no extra call, so the cost is zero.
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
            # Returned stably sorted by key -- a caller that needs chronological order sorts
            # again, and the order of entries with the same time does not wobble between
            # runs.
            return sorted(keys, key=lambda item: item[0])

        return await asyncio.to_thread(_list)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under a namespace-relative prefix (in batches of 1000).

        For the project deletion path only -- a list followed by delete_objects is not atomic,
        but deletion is idempotent so a partial failure converges on a repeat call."""
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
