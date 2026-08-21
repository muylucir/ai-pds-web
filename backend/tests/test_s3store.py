import boto3
import pytest
from moto import mock_aws
from aipds.s3store import S3Store
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

async def test_fake_list_with_etags_changes_when_content_changes():
    s3 = FakeS3Store()
    await s3.put("aiplc-docs/a.md", "one")
    first = await s3.list_with_etags("aiplc-docs/")
    await s3.put("aiplc-docs/a.md", "two")
    second = await s3.list_with_etags("aiplc-docs/")
    assert first[0][0] == second[0][0] == "aiplc-docs/a.md"
    assert first[0][1] != second[0][1]

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

async def test_s3store_list_with_etags_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        put_etag = await store.put("aiplc-docs/a.md", "one")
        assert await store.list_with_etags("aiplc-docs/") == [
            ("aiplc-docs/a.md", put_etag)
        ]

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


# ---- list_with_times: 목록을 최신 순으로 정렬하기 위한 시각 ----
#
# 페이크는 쓰기 순번을 "시각"으로 쓰고(결정론), 실물은 S3의 `LastModified`를 쓴다.
# **구현이 다르므로 실물도 따로 고정한다** — 페이크만 검사하면 실물이 0.0을 돌려주는
# 회귀를 못 잡고, 그러면 `Workspace.list_artifacts`가 조용히 알파벳 순으로 되돌아간다.

async def test_s3store_list_with_times_reports_last_modified_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        await store.put("aiplc-docs/audit.md", "감사")
        await store.put("aiplc-docs/prfaq.md", "PRFAQ")

        times = dict(await store.list_with_times("aiplc-docs/"))

        assert set(times) == {"aiplc-docs/audit.md", "aiplc-docs/prfaq.md"}
        # 0.0이면 LastModified를 못 읽은 것이다 — 그 상태로는 정렬이 무의미해진다.
        assert all(t > 0 for t in times.values()), times


async def test_s3store_list_with_etags_still_works_after_the_split_moto():
    """`list_with_etags`가 `_list_meta`를 공유하게 바뀌었다 — 복원 경로
    (runner._restore_workspace_from_s3)가 그것으로 변경 파일을 고르므로 회귀하면
    매 턴 전체를 다시 내려받거나 아무것도 안 받는다."""
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        await store.put("aiplc-docs/audit.md", "감사")
        first = dict(await store.list_with_etags("aiplc-docs/"))
        await store.put("aiplc-docs/audit.md", "감사 고침")
        second = dict(await store.list_with_etags("aiplc-docs/"))

        assert first["aiplc-docs/audit.md"], "ETag가 비었다"
        assert first["aiplc-docs/audit.md"] != second["aiplc-docs/audit.md"]
