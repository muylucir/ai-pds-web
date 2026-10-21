# backend/tests/test_import_staging.py — staging of uploaded bundles.
from __future__ import annotations

import re

import pytest
from botocore.exceptions import ClientError

from aipds.import_staging import (
    CONTENT_TYPE,
    MAX_IMPORT_BYTES,
    STAGING_PREFIX,
    ImportStaging,
    new_upload_id,
)

BUCKET = "artifacts-bucket"


class _StubS3Client:
    """Minimal stub that imitates only presign/head/download/delete."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = dict(objects or {})
        self.presign_calls: list[dict] = []
        self.deleted: list[str] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign_calls.append({"operation": operation, "params": Params,
                                   "expires_in": ExpiresIn})
        return f"https://{Params['Bucket']}.s3.example.invalid/{Params['Key']}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def download_file(self, Bucket, Key, path):
        with open(path, "wb") as fh:
            fh.write(self.objects[Key])

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)
        return {}


def _staging(objects=None) -> tuple[ImportStaging, _StubS3Client]:
    client = _StubS3Client(objects)
    return ImportStaging(bucket=BUCKET, client=client), client


def test_upload_ids_are_unguessable_hex():
    """This value becomes the S3 key — if the client could choose it, both traversal and
    overwriting someone else's staging would be open."""
    ids = {new_upload_id() for _ in range(50)}

    assert len(ids) == 50
    assert all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)


def test_the_key_lives_outside_the_project_keyspace():
    """Placing it under `projects/` would leave room for the restore_projects scan and
    project deletion's delete_prefix to mistake this key for their own."""
    staging, _ = _staging()

    key = staging.key("a" * 32)

    assert key == f"{STAGING_PREFIX}{'a' * 32}"
    assert not key.startswith("projects/")


async def test_presign_signs_the_content_type_but_not_the_length():
    """`Content-Length` is a header the browser controls directly — putting it in the
    signature creates a per-browser signature-mismatch trap, and head_object measures the size."""
    staging, client = _staging()

    url = await staging.presign_put("b" * 32, expires_in=600)

    assert url.startswith("https://")
    call = client.presign_calls[0]
    assert call["operation"] == "put_object"
    assert call["expires_in"] == 600
    assert call["params"] == {"Bucket": BUCKET,
                             "Key": f"{STAGING_PREFIX}{'b' * 32}",
                             "ContentType": CONTENT_TYPE}
    assert "ContentLength" not in call["params"]


async def test_size_reads_the_actual_object():
    staging, _ = _staging({f"{STAGING_PREFIX}{'c' * 32}": b"0123456789"})

    assert await staging.size("c" * 32) == 10


async def test_size_is_none_when_the_upload_never_arrived():
    """The signature was issued but no PUT arrived — what the user needs to do is upload
    again, not pick a different file."""
    staging, _ = _staging()

    assert await staging.size("d" * 32) is None


async def test_size_propagates_other_s3_errors():
    """Demoting a 403 (permissions) to 'no upload' makes a wiring mistake look like a user error."""
    staging, client = _staging()

    def _boom(Bucket, Key):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")

    client.head_object = _boom

    with pytest.raises(ClientError):
        await staging.size("e" * 32)


async def test_download_writes_the_bundle_to_disk(tmp_path):
    staging, _ = _staging({f"{STAGING_PREFIX}{'f' * 32}": b"PK\x03\x04zip"})
    dest = tmp_path / "bundle.zip"

    await staging.download_to("f" * 32, dest)

    assert dest.read_bytes() == b"PK\x03\x04zip"


async def test_delete_removes_the_staged_object():
    staging, client = _staging({f"{STAGING_PREFIX}{'a' * 32}": b"x"})

    await staging.delete("a" * 32)

    assert client.deleted == [f"{STAGING_PREFIX}{'a' * 32}"]


async def test_delete_swallows_failures():
    """A successful import must not become a 500 because cleanup failed — the lifecycle
    rule on imports/ sweeps up leftover objects."""
    staging, client = _staging()

    def _boom(Bucket, Key):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")

    client.delete_object = _boom

    await staging.delete("a" * 32)  # does not raise


def test_the_upload_cap_is_generous_but_finite():
    """It is a presigned PUT, so nginx is not involved; the real constraint is the temp disk."""
    assert 64 * 1024 * 1024 <= MAX_IMPORT_BYTES <= 4 * 1024 * 1024 * 1024


async def test_the_real_client_signs_for_the_regional_endpoint(monkeypatch):
    """The global endpoint 307s a new bucket outside us-east-1 to the regional one, without
    CORS headers — the browser blocks a fresh deployment's first import."""
    from urllib.parse import urlparse

    from aipds import app as app_module

    monkeypatch.setenv("AIPDS_S3_BUCKET", BUCKET)
    monkeypatch.setenv("AIPDS_S3_REGION", "ap-northeast-2")
    # Signing is local, but botocore still needs credentials to sign with.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")  # nosec B105 - a fake credential
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    app_module.s3_presign_client.cache_clear()
    try:
        url = await app_module.import_staging().presign_put("u1")
    finally:
        app_module.s3_presign_client.cache_clear()
    assert urlparse(url).netloc == f"{BUCKET}.s3.ap-northeast-2.amazonaws.com"

