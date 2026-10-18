import pytest
from aipds.s3store import S3Store
from tests.fakes.in_memory_s3 import FakeS3Store


class _StubS3Client:
    """Minimal stub that mimics only the list_objects_v2 paginator + delete_objects."""

    def __init__(self, keys: list[str]):
        self.objects = {k: "x" for k in keys}
        self.delete_calls: list[int] = []  # records the batch size per call

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class _P:
            def paginate(self, Bucket, Prefix):
                matched = [k for k in sorted(client.objects) if k.startswith(Prefix)]
                # split pages into chunks of 700 to simulate more than 1000 objects
                for i in range(0, len(matched), 700):
                    yield {"Contents": [{"Key": k} for k in matched[i:i + 700]]}
                if not matched:
                    yield {}

        return _P()

    def delete_objects(self, Bucket, Delete):
        batch = [o["Key"] for o in Delete["Objects"]]
        assert len(batch) <= 1000  # S3 API limit
        self.delete_calls.append(len(batch))
        for k in batch:
            self.objects.pop(k, None)
        return {}


@pytest.mark.asyncio
async def test_delete_prefix_removes_only_namespaced_prefix():
    stub = _StubS3Client(["sessions/session_a/m1.json", "sessions/session_a/m2.json",
                          "sessions/session_b/m1.json"])
    store = S3Store(bucket="b", prefix="sessions/", client=stub)
    n = await store.delete_prefix("session_a/")
    assert n == 2
    assert list(stub.objects) == ["sessions/session_b/m1.json"]


@pytest.mark.asyncio
async def test_delete_prefix_batches_over_1000():
    keys = [f"projects/p1/f{i:04}" for i in range(1500)]
    stub = _StubS3Client(keys)
    store = S3Store(bucket="b", prefix="projects/", client=stub)
    n = await store.delete_prefix("p1/")
    assert n == 1500
    assert sum(stub.delete_calls) == 1500
    assert max(stub.delete_calls) <= 1000 and len(stub.delete_calls) >= 2


@pytest.mark.asyncio
async def test_delete_prefix_raises_on_delete_objects_errors():
    """Must detect the Errors array in the delete_objects response and raise RuntimeError."""
    class _StubS3ClientWithErrors(_StubS3Client):
        def delete_objects(self, Bucket, Delete):
            batch = [o["Key"] for o in Delete["Objects"]]
            assert len(batch) <= 1000
            self.delete_calls.append(len(batch))
            # in the first batch, return an error for the first key only (InternalError)
            if len(self.delete_calls) == 1:
                return {
                    "Errors": [
                        {
                            "Key": batch[0],
                            "Code": "InternalError",
                            "Message": "We encountered an internal error. Please try again.",
                        }
                    ]
                }
            # later batches delete normally
            for k in batch:
                self.objects.pop(k, None)
            return {}

    keys = [f"projects/p1/f{i:04}" for i in range(10)]
    stub = _StubS3ClientWithErrors(keys)
    store = S3Store(bucket="b", prefix="projects/", client=stub)

    with pytest.raises(RuntimeError) as exc_info:
        await store.delete_prefix("p1/")

    error_msg = str(exc_info.value)
    assert "delete_prefix:" in error_msg
    assert "1/10" in error_msg  # 1 failed out of 10
    assert "InternalError" in error_msg


@pytest.mark.asyncio
async def test_fake_store_delete_prefix_matches_contract():
    fake = FakeS3Store()
    fake.blobs["session_a/m1"] = "x"
    fake.blobs["session_a/m2"] = "x"
    fake.blobs["session_b/m1"] = "x"
    assert await fake.delete_prefix("session_a/") == 2
    assert list(fake.blobs) == ["session_b/m1"]
    assert await fake.delete_prefix("session_a/") == 0  # idempotent
