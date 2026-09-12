# backend/tests/test_import_staging.py — 업로드된 번들의 스테이징.
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
    """presign/head/download/delete만 흉내내는 최소 스텁."""

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
    """이 값이 S3 키가 된다 — 클라이언트가 정할 수 있으면 traversal과 남의
    스테이징 덮어쓰기가 둘 다 열린다."""
    ids = {new_upload_id() for _ in range(50)}

    assert len(ids) == 50
    assert all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)


def test_the_key_lives_outside_the_project_keyspace():
    """`projects/` 아래에 두면 restore_projects의 스캔과 프로젝트 삭제의
    delete_prefix가 이 키를 자기 것으로 착각할 여지가 생긴다."""
    staging, _ = _staging()

    key = staging.key("a" * 32)

    assert key == f"{STAGING_PREFIX}{'a' * 32}"
    assert not key.startswith("projects/")


async def test_presign_signs_the_content_type_but_not_the_length():
    """`Content-Length`는 브라우저가 직접 제어하는 헤더다 — 서명에 넣으면
    브라우저별 서명 불일치 함정이 생기고, 크기는 head_object가 실측한다."""
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
    """서명은 발급됐지만 PUT이 오지 않은 상태 — 사용자가 할 일은 다른 파일을
    고르는 것이 아니라 다시 올리는 것이다."""
    staging, _ = _staging()

    assert await staging.size("d" * 32) is None


async def test_size_propagates_other_s3_errors():
    """403(권한)을 '업로드가 없다'로 강등하면 배선 실수가 사용자 오류처럼 보인다."""
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
    """성공한 임포트가 정리 실패 때문에 500이 되면 안 된다 — 남은 객체는
    imports/ 의 lifecycle 규칙이 걷는다."""
    staging, client = _staging()

    def _boom(Bucket, Key):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")

    client.delete_object = _boom

    await staging.delete("a" * 32)  # 던지지 않는다


def test_the_upload_cap_is_generous_but_finite():
    """presigned PUT이라 nginx가 관여하지 않으므로 실제 제약은 임시 디스크다."""
    assert 64 * 1024 * 1024 <= MAX_IMPORT_BYTES <= 4 * 1024 * 1024 * 1024
