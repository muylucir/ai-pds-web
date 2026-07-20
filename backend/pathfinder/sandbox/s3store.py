from __future__ import annotations
import asyncio
from typing import Protocol

from botocore.exceptions import ClientError


class S3StoreLike(Protocol):
    async def get(self, key: str) -> str: ...
    async def put(self, key: str, content: str) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def delete_prefix(self, prefix: str) -> int: ...


class S3Store:
    """Durable blob store over S3 (Seoul, ap-northeast-2). Thin: text in/out,
    workspace-relative keys namespaced under `prefix`. Path-safety and key
    composition are the caller's (MicroVMSandbox) job. boto3 is synchronous, so
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

    async def put(self, key: str, content: str) -> None:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._full_key(key),
                Body=content.encode("utf-8"),
            )

        await asyncio.to_thread(_put)

    async def list(self, prefix: str) -> list[str]:
        def _list() -> list[str]:
            full = self._full_key(prefix)
            paginator = self._client.get_paginator("list_objects_v2")
            keys: list[str] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=full):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"][len(self._prefix):])  # strip namespace
            return sorted(keys)

        return await asyncio.to_thread(_list)

    async def delete_prefix(self, prefix: str) -> int:
        """네임스페이스 내 상대 prefix 이하 오브젝트 전량 삭제(1000개 배치).

        프로젝트 삭제 경로 전용 — list 후 delete_objects라 원자적이진 않지만
        삭제는 멱등이므로 부분 실패 시 재호출로 수렴한다."""
        def _delete() -> int:
            full = self._full_key(prefix)
            paginator = self._client.get_paginator("list_objects_v2")
            keys = [obj["Key"]
                    for page in paginator.paginate(Bucket=self._bucket, Prefix=full)
                    for obj in page.get("Contents", [])]
            for i in range(0, len(keys), 1000):  # S3 delete_objects 상한
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                            "Quiet": True})
            return len(keys)

        return await asyncio.to_thread(_delete)
