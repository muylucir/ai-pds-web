from __future__ import annotations

from pathlib import Path


class FakeImportStaging:
    """In-memory ImportStaging for import route tests (no boto3, no AWS).

    `objects`는 upload_id → 바이트다. 테스트는 `objects[uid] = zip_bytes`로
    "브라우저가 presigned URL로 PUT을 마쳤다"를 표현한다 — 그 PUT은 백엔드를
    지나지 않으므로 흉내낼 HTTP가 없다.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.presigned: list[str] = []

    async def presign_put(self, upload_id: str, **_kw) -> str:
        self.presigned.append(upload_id)
        return f"https://bucket.s3.example.invalid/imports/{upload_id}"

    async def size(self, upload_id: str) -> int | None:
        body = self.objects.get(upload_id)
        return None if body is None else len(body)

    async def download_to(self, upload_id: str, dest: Path) -> None:
        dest.write_bytes(self.objects[upload_id])

    async def delete(self, upload_id: str) -> None:
        self.objects.pop(upload_id, None)
