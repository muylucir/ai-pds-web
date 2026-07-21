import boto3
import pytest
from moto import mock_aws
from pathfinder.s3store import S3Store
from fakes.in_memory_s3 import FakeS3Store

# ---- FakeS3Store (the in-memory double used by runner/route tests) ----

async def test_fake_put_get_roundtrip():
    s3 = FakeS3Store()
    await s3.put("aiplc-docs/audit.md", "hello")
    assert await s3.get("aiplc-docs/audit.md") == "hello"

async def test_fake_get_missing_raises_filenotfound():
    s3 = FakeS3Store()
    with pytest.raises(FileNotFoundError):
        await s3.get("aiplc-docs/nope.md")

async def test_fake_list_returns_sorted_keys_under_prefix():
    s3 = FakeS3Store()
    await s3.put("aiplc-docs/b.md", "1")
    await s3.put("aiplc-docs/a.md", "2")
    await s3.put("prototype/app.py", "3")
    assert await s3.list("aiplc-docs/") == ["aiplc-docs/a.md", "aiplc-docs/b.md"]

# ---- Real S3Store against moto (proves the boto3 data-plane wiring) ----

@mock_aws
def _make_bucket(name: str, region: str = "ap-northeast-2"):
    client = boto3.client("s3", region_name=region)
    client.create_bucket(
        Bucket=name,
        CreateBucketConfiguration={"LocationConstraint": region},
    )
    return client

async def test_s3store_put_get_roundtrip_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        await store.put("aiplc-docs/audit.md", "안녕하세요")  # non-ASCII round-trips
        assert await store.get("aiplc-docs/audit.md") == "안녕하세요"

async def test_s3store_get_missing_raises_filenotfound_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        with pytest.raises(FileNotFoundError):
            await store.get("aiplc-docs/missing.md")

async def test_s3store_list_strips_prefix_and_sorts_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        await store.put("aiplc-docs/b.md", "1")
        await store.put("aiplc-docs/a.md", "2")
        assert await store.list("aiplc-docs/") == ["aiplc-docs/a.md", "aiplc-docs/b.md"]

async def test_s3store_keys_are_namespaced_by_prefix_moto():
    # Two projects share a bucket but must not see each other's keys.
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        p1 = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        p2 = S3Store(bucket="pf-bucket", prefix="projects/p2/", client=client)
        await p1.put("aiplc-docs/x.md", "one")
        assert await p2.list("aiplc-docs/") == []
        with pytest.raises(FileNotFoundError):
            await p2.get("aiplc-docs/x.md")
