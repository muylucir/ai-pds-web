# backend/aipds/import_staging.py — where an uploaded bundle lives until it is imported.
#
# **Why the browser PUTs directly to S3.** A bundle is the whole of one project, so its size has
# no upper bound. Requests that pass through our servers do have caps — nginx's
# `client_max_body_size` (set to match the attachment upload cap, infra/lib/user-data.ts) and the Next
# middleware body cap (it truncates and passes on anything over, frontend/next.config.mjs). A presigned
# PUT keeps the bundle from passing through nginx, the Next proxy or this process, making those caps
# irrelevant.
#
# **Why a separate class rather than `S3StoreLike`.** Presigning is not a blob operation. Putting it in that
# protocol would make every test fake imitate URLs (12 files currently use that
# fake), and the three things needed here — signing, checking size with `head_object`, and managed
# multipart download — all require the boto3 client directly.
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from botocore.exceptions import ClientError

_log = logging.getLogger(__name__)

#: Staging prefix at the bucket root. A place that does not mix with project data —
#: under `projects/`, both the `restore_projects` scan and project deletion's
#: `delete_prefix` could mistake these keys for their own.
#: The same value must be in `BACKEND_BUCKET_PREFIXES` in `infra/lib/backend-permissions.ts` for
#: the signature to be valid — otherwise the browser's PUT gets 403, and that failure is not
#: in the backend log (signing succeeds and S3 refuses the browser).
STAGING_PREFIX = "imports/"

#: Upload cap. It is a presigned PUT, so nginx is not involved; the real constraint is the temporary disk
#: where the import unpacks the bundle (project_import downloads the staging object to a temp file
#: and opens it with `zipfile` — to keep memory bounded).
MAX_IMPORT_BYTES = 512 * 1024 * 1024

#: Signature validity period. There must be time to upload hundreds of MB on a workshop network, and no reason
#: for it to be longer — this URL is a credential that can write to the bucket.
PRESIGN_EXPIRES_IN = 900

#: Content type included in the signature. The browser must PUT with this value for the signature to match —
#: hence `content-type` must be in the bucket CORS `allowedHeaders`.
CONTENT_TYPE = "application/zip"


def new_upload_id() -> str:
    """The name of the staging key. **The backend decides it.**

    If the client chose the key, both traversal (`../projects/victim/project.json`) and overwriting someone
    else's staging would open up. With uuid4 both are structurally impossible, and it cannot
    be guessed either.
    """
    return uuid.uuid4().hex


class ImportStaging:
    """The life of one uploaded bundle: sign → check size → download → delete."""

    def __init__(self, bucket: str, client, prefix: str = STAGING_PREFIX) -> None:
        self._bucket = bucket
        self._client = client
        self._prefix = prefix

    def key(self, upload_id: str) -> str:
        return f"{self._prefix}{upload_id}"

    async def presign_put(self, upload_id: str, *,
                          expires_in: int = PRESIGN_EXPIRES_IN) -> str:
        """The URL the browser PUTs to.

        `ContentLength` is not signed. It is a header the browser controls directly, so
        signing it creates browser-specific traps of preflight and signature mismatch, and the size
        has to be measured with `size()` anyway — the value the client reports is a convenience, and
        `head_object` is the authority.

        Why `to_thread`: signing itself does not touch the network, but freezing the credentials
        can hit IMDS on the first call.
        """
        def _sign() -> str:
            return self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self._bucket, "Key": self.key(upload_id),
                        "ContentType": CONTENT_TYPE},
                ExpiresIn=expires_in)

        return await asyncio.to_thread(_sign)

    async def size(self, upload_id: str) -> int | None:
        """The actual byte count of the uploaded object. None if missing.

        **This is the authoritative size check.** The point is to ask before downloading —
        pulling an over-cap object onto disk and then rejecting it is not a defense.
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
        """Staging object to a local file. It is boto3's managed transfer, so it is split into
        multipart and never holds the whole thing in memory."""
        await asyncio.to_thread(self._client.download_file, self._bucket,
                                self.key(upload_id), str(dest))

    async def delete(self, upload_id: str) -> None:
        """Deletes the staging object. Idempotent.

        Swallows failures: the import's success or failure is already decided, and leftover objects
        are swept by the lifecycle rule on `imports/`. Throwing here would turn a successful import
        into a 500 because of a cleanup failure.
        """
        try:
            await asyncio.to_thread(self._client.delete_object,
                                    Bucket=self._bucket, Key=self.key(upload_id))
        except Exception:
            _log.warning("could not delete staged bundle %s", self.key(upload_id),
                         exc_info=True)
