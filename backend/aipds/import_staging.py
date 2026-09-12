# backend/aipds/import_staging.py — 업로드된 번들이 임포트되기 전까지 사는 곳.
#
# **왜 브라우저가 S3로 직접 PUT하는가.** 번들은 프로젝트 하나의 전부이므로 5MB
# 업로드 상한 안에 들어오지 않는다. 그런데 그 상한은 우리 코드가 아니라 nginx의
# `client_max_body_size 6m`이고(infra/lib/user-data.ts), 그 값은 **인스턴스
# user-data 안에** 있다 — 고치는 것이 곧 EC2 교체이고, 교체는 그 박스에 있는 모든
# 프로토타입 빌드 트리를 가져간다. presigned PUT은 번들이 nginx도 Next 프록시도
# 이 프로세스도 지나지 않게 해서 그 상한을 무관하게 만든다.
#
# **왜 `S3StoreLike`가 아닌 별도 클래스인가.** presign은 blob 연산이 아니다. 그
# 프로토콜에 넣으면 모든 테스트 페이크가 URL을 흉내내야 하고(현재 12개 파일이 그
# 페이크를 쓴다), 여기서 필요한 세 가지 — 서명, `head_object`로 크기 확인, 관리형
# 멀티파트 다운로드 — 는 전부 boto3 클라이언트를 직접 요구한다.
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from botocore.exceptions import ClientError

_log = logging.getLogger(__name__)

#: 버킷 루트의 스테이징 prefix. 프로젝트 데이터와 섞이지 않는 자리다 —
#: `projects/` 아래에 두면 `restore_projects`의 스캔과 프로젝트 삭제의
#: `delete_prefix`가 둘 다 이 키를 자기 것으로 착각할 여지가 생긴다.
#: `infra/lib/backend-permissions.ts`의 `BACKEND_BUCKET_PREFIXES`에 같은 값이 있어야
#: 서명이 유효하다 — 없으면 브라우저의 PUT이 403이고, 그 실패는 백엔드 로그에
#: 남지 않는다(서명은 성공하고 거절은 S3가 브라우저에게 한다).
STAGING_PREFIX = "imports/"

#: 업로드 상한. presigned PUT이라 nginx가 관여하지 않으므로 실제 제약은 임포트가
#: 번들을 풀어 놓는 임시 디스크다(project_import는 스테이징 객체를 임시 파일로
#: 내려 `zipfile`로 연다 — 메모리를 유계로 두려고).
MAX_IMPORT_BYTES = 512 * 1024 * 1024

#: 서명 유효 시간. 워크숍 네트워크에서 수백 MB를 올릴 시간은 있어야 하고, 그보다
#: 길 이유는 없다 — 이 URL은 버킷에 쓸 수 있는 자격증명이다.
PRESIGN_EXPIRES_IN = 900

#: 서명에 포함하는 콘텐츠 타입. 브라우저가 이 값으로 PUT해야 서명이 맞는다 —
#: 그래서 버킷 CORS의 `allowedHeaders`에 `content-type`이 있어야 한다.
CONTENT_TYPE = "application/zip"


def new_upload_id() -> str:
    """스테이징 키의 이름. **백엔드가 정한다.**

    클라이언트가 키를 정하면 traversal(`../projects/victim/project.json`)과 남의
    스테이징 덮어쓰기가 둘 다 열린다. uuid4면 그 둘이 구조적으로 불가능하고,
    추측도 되지 않는다.
    """
    return uuid.uuid4().hex


class ImportStaging:
    """업로드된 번들 하나의 수명: 서명 → 크기 확인 → 내려받기 → 삭제."""

    def __init__(self, bucket: str, client, prefix: str = STAGING_PREFIX) -> None:
        self._bucket = bucket
        self._client = client
        self._prefix = prefix

    def key(self, upload_id: str) -> str:
        return f"{self._prefix}{upload_id}"

    async def presign_put(self, upload_id: str, *,
                          expires_in: int = PRESIGN_EXPIRES_IN) -> str:
        """브라우저가 PUT할 URL.

        `ContentLength`는 서명하지 않는다. 브라우저가 직접 제어하는 헤더라
        서명에 넣으면 preflight·서명 불일치의 브라우저별 함정이 생기고, 크기는
        어차피 `size()`로 실측해야 한다 — 클라이언트가 알린 값은 편의이고
        `head_object`가 권위다.

        `to_thread`인 이유: 서명 자체는 네트워크를 타지 않지만 자격증명을 얼리는
        과정이 첫 호출에서 IMDS를 때릴 수 있다.
        """
        def _sign() -> str:
            return self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self._bucket, "Key": self.key(upload_id),
                        "ContentType": CONTENT_TYPE},
                ExpiresIn=expires_in)

        return await asyncio.to_thread(_sign)

    async def size(self, upload_id: str) -> int | None:
        """업로드된 객체의 실제 바이트 수. 없으면 None.

        **이것이 권위 있는 크기 검사다.** 내려받기 전에 물어보는 것이 요점이다 —
        상한을 넘는 객체를 디스크로 끌어온 다음에 거절하면 방어가 아니다.
        """
        def _head() -> int | None:
            try:
                resp = self._client.head_object(Bucket=self._bucket,
                                                Key=self.key(upload_id))
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise
            return int(resp["ContentLength"])

        return await asyncio.to_thread(_head)

    async def download_to(self, upload_id: str, dest: Path) -> None:
        """스테이징 객체를 로컬 파일로. boto3의 관리형 전송이라 멀티파트로
        나뉘고 메모리에 전체를 담지 않는다."""
        await asyncio.to_thread(self._client.download_file, self._bucket,
                                self.key(upload_id), str(dest))

    async def delete(self, upload_id: str) -> None:
        """스테이징 객체를 지운다. 멱등.

        실패를 삼킨다: 임포트의 성공/실패는 이미 결정됐고, 남은 객체는
        `imports/` 의 lifecycle 규칙이 걷는다. 여기서 던지면 성공한 임포트가
        정리 실패 때문에 500이 된다.
        """
        try:
            await asyncio.to_thread(self._client.delete_object,
                                    Bucket=self._bucket, Key=self.key(upload_id))
        except Exception:
            _log.warning("could not delete staged bundle %s", self.key(upload_id),
                         exc_info=True)
