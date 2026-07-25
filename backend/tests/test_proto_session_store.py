# backend/tests/test_proto_session_store.py
from __future__ import annotations

from pathfinder.proto.session_store import S3SessionStore, transcript_prefix

from fakes.in_memory_s3 import FakeS3Store

SLUG = "todo-app"
SID = "11111111-2222-3333-4444-555555555555"
KEY = {"project_key": "proj-1/todo-app", "session_id": SID}


async def test_append_then_load_round_trips_entries_in_order():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)

    await store.append(KEY, [{"type": "user", "uuid": "u1"},
                             {"type": "assistant", "uuid": "a1"}])
    await store.append(KEY, [{"type": "assistant", "uuid": "a2"}])

    loaded = await store.load(KEY)
    assert [e["uuid"] for e in loaded] == ["u1", "a1", "a2"]


async def test_load_returns_none_for_a_session_never_written():
    store = S3SessionStore(FakeS3Store(), slug=SLUG)
    assert await store.load(KEY) is None


async def test_entries_survive_non_ascii_and_nesting():
    """Entries are opaque JSON blobs -- the only invariant the SDK requires is
    a json round-trip, so Korean text and nested objects must come back deep-
    equal."""
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    entry = {"type": "user", "uuid": "u1",
             "message": {"content": [{"type": "text", "text": "버튼 색 바꿔줘"}]}}

    await store.append(KEY, [entry])

    assert (await store.load(KEY)) == [entry]


async def test_batches_land_under_the_prototype_transcript_prefix():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    await store.append(KEY, [{"type": "user", "uuid": "u1"}])
    assert all(k.startswith(transcript_prefix(SLUG)) for k in s3.blobs)


async def test_subagent_subpath_is_stored_and_listed_separately():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    sub = {**KEY, "subpath": "subagents/agent-7"}

    await store.append(KEY, [{"type": "user", "uuid": "u1"}])
    await store.append(sub, [{"type": "user", "uuid": "s1"}])

    assert [e["uuid"] for e in await store.load(KEY)] == ["u1"]
    assert [e["uuid"] for e in await store.load(sub)] == ["s1"]
    assert await store.list_subkeys(KEY) == ["subagents/agent-7"]


async def test_list_subkeys_empty_when_no_subagents():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    await store.append(KEY, [{"type": "user", "uuid": "u1"}])
    assert await store.list_subkeys(KEY) == []


async def test_sessions_do_not_bleed_across_session_ids():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    other = {**KEY, "session_id": "99999999-8888-7777-6666-555555555555"}

    await store.append(KEY, [{"type": "user", "uuid": "u1"}])

    assert await store.load(other) is None


async def test_round_trip_over_the_real_S3Store_shape():
    """FakeS3Store could drift from S3Store's contract (key namespacing,
    sorted list, FileNotFoundError). Run the same round trip against a real
    boto3 client backed by moto."""
    import boto3
    from moto import mock_aws
    from pathfinder.s3store import S3Store

    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-test",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        s3 = S3Store(bucket="pf-test", prefix="projects/proj-1/", client=client)
        store = S3SessionStore(s3, slug=SLUG)

        await store.append(KEY, [{"type": "user", "uuid": "u1"}])
        await store.append(KEY, [{"type": "assistant", "uuid": "a1"}])

        assert [e["uuid"] for e in await store.load(KEY)] == ["u1", "a1"]
        assert await store.load({**KEY, "session_id": SID.replace("1", "7")}) is None
