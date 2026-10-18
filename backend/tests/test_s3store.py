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


# ---- list_with_times: timestamps for sorting listings newest first ----
#
# The fake uses the write sequence number as the "time" (deterministic), while the real
# one uses S3's `LastModified`. **Because the implementations differ, the real one is
# pinned separately too** — checking only the fake would miss a regression where the
# real one returns 0.0, and then `Workspace.list_artifacts` would silently revert to
# alphabetical order.

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
        # 0.0 means LastModified could not be read — in that state sorting becomes
        # meaningless.
        assert all(t > 0 for t in times.values()), times


async def test_s3store_list_with_etags_still_works_after_the_split_moto():
    """`list_with_etags` was changed to share `_list_meta` — the restore path
    (runner._restore_workspace_from_s3) uses it to pick changed files, so a regression
    would mean re-downloading everything every turn or downloading nothing."""
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


# ---- get_cached: a body found by ETag is not fetched again ----

class _CountingClient:
    """Wraps the boto3 client and counts only get_object calls."""

    def __init__(self, inner):
        self._inner = inner
        self.gets = 0

    def get_object(self, **kw):
        self.gets += 1
        return self._inner.get_object(**kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _moto_store():
    client = boto3.client("s3", region_name="ap-northeast-2")
    client.create_bucket(
        Bucket="cache-bucket",
        CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
    )
    counting = _CountingClient(client)
    return S3Store(bucket="cache-bucket", prefix="sessions/", client=counting), counting


async def test_get_cached_skips_s3_for_a_known_etag_moto():
    with mock_aws():
        store, counting = _moto_store()
        await store.put("t/main/0001.jsonl", '{"a": 1}')
        [(key, etag)] = await store.list_with_etags("t/main/")

        assert await store.get_cached(key, etag) == '{"a": 1}'
        assert await store.get_cached(key, etag) == '{"a": 1}'
        assert counting.gets == 1


async def test_get_cached_never_serves_an_overwritten_body_moto():
    """Writing the same key again changes its ETag — the old body cannot be found under
    the new ETag. Importing under the same id after a project is deleted takes this
    shape."""
    with mock_aws():
        store, counting = _moto_store()
        await store.put("t/main/0001.jsonl", "old")
        [(key, old_etag)] = await store.list_with_etags("t/main/")
        assert await store.get_cached(key, old_etag) == "old"

        await store.put("t/main/0001.jsonl", "new")
        [(_, new_etag)] = await store.list_with_etags("t/main/")
        assert new_etag != old_etag
        assert await store.get_cached(key, new_etag) == "new"
        assert counting.gets == 2


async def test_get_cached_files_the_body_under_the_etag_s3_returned_moto():
    """If the object changes between the listing and the GET, the listing's ETag is
    stale. If the new body were stored under the stale ETag name, a call that asks for
    that ETag again would get the wrong body."""
    with mock_aws():
        store, counting = _moto_store()
        await store.put("t/main/0001.jsonl", "v1")
        [(key, stale)] = await store.list_with_etags("t/main/")
        await store.put("t/main/0001.jsonl", "v2")      # races the reader

        assert await store.get_cached(key, stale) == "v2"
        # Asking again with the stale ETag goes to S3, not the cache.
        await store.get_cached(key, stale)
        assert counting.gets == 2


def test_body_cache_evicts_least_recently_used_over_budget():
    from aipds.s3store import _BodyCache
    cache = _BodyCache(max_bytes=10)
    cache.put(("b", "k1", "e"), "aaaa")
    cache.put(("b", "k2", "e"), "bbbb")
    assert cache.get(("b", "k1", "e")) == "aaaa"     # k1 becomes most recent
    cache.put(("b", "k3", "e"), "cccc")              # 12 > 10 → k2 is evicted
    assert cache.get(("b", "k2", "e")) is None
    assert cache.get(("b", "k1", "e")) == "aaaa"
    assert cache.get(("b", "k3", "e")) == "cccc"
    cache.put(("b", "huge", "e"), "x" * 11)          # larger than the budget: not cached
    assert cache.get(("b", "huge", "e")) is None
