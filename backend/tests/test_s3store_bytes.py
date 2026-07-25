# backend/tests/test_s3store_bytes.py — binary-safe path for prototype bundles.
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from pathfinder.s3store import S3Store

PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xfd"


def _store(client):
    return S3Store(bucket="pf-test", prefix="projects/p1/", client=client)


@pytest.fixture
def client():
    with mock_aws():
        c = boto3.client("s3", region_name="ap-northeast-2")
        c.create_bucket(
            Bucket="pf-test",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        yield c


async def test_put_bytes_get_bytes_round_trips_binary_unchanged(client):
    """The text API mangles this: .decode('utf-8', errors='replace') turns
    non-UTF-8 bytes into U+FFFD, which is why prototype images and fonts came
    back corrupt from the S3 bundle."""
    store = _store(client)
    await store.put_bytes("prototypes/x/bundle/logo.png", PNG_HEADER)
    assert await store.get_bytes("prototypes/x/bundle/logo.png") == PNG_HEADER


async def test_get_bytes_raises_file_not_found_like_get(client):
    with pytest.raises(FileNotFoundError):
        await _store(client).get_bytes("prototypes/x/bundle/missing.png")


async def test_text_api_still_works_alongside(client):
    store = _store(client)
    await store.put("aiplc-docs/a.md", "# 한글 문서")
    assert await store.get("aiplc-docs/a.md") == "# 한글 문서"


async def test_bytes_and_text_share_one_namespace(client):
    """put_bytes must land on the same key the text API would use, so listing
    and delete_prefix keep working across both."""
    store = _store(client)
    await store.put_bytes("prototypes/x/bundle/a.bin", b"\x00\x01")
    assert await store.list("prototypes/x/bundle/") == ["prototypes/x/bundle/a.bin"]
