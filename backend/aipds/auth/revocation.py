# backend/aipds/auth/revocation.py — rejects tokens of users an admin cut off, before they expire.
#
# The backend verifies access tokens only locally (signature and expiry, auth/verifier.py). So even when an admin
# disables, deletes, or demotes a user or resets their password, the access token that user already holds
# keeps passing until it expires (ACCESS_TOKEN_VALIDITY_MINUTES, infra/lib/aipds-auth-stack.ts).
# Cognito's GlobalSignOut does not change this either — local verification does not ask Cognito.
# The refresh side is blocked by Cognito: refresh fails for disabled or deleted users, and demoted users
# receive tokens carrying the new groups. The only remaining hole is access tokens already issued.
#
# So for each user we record "reject tokens issued (`iat`) before this time". The record is meaningful
# only for the token lifetime, so it is discarded after that. With a single instance a local file is enough,
# and since updates restart the backend, memory alone is not.
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

_log = logging.getLogger(__name__)

PATH_ENV = "AIPDS_REVOCATIONS_FILE"

#: How long a record is kept. Longer than the access token lifetime (180 minutes) — tokens older than that
#: are rejected by expiry anyway.
KEEP_SECONDS = 4 * 3600


def default_path() -> Path:
    return Path(os.environ.get(PATH_ENV, "~/aipds-auth/revoked.json")).expanduser()


class Revocations:
    """username → rejection cutoff time (epoch seconds). Writes go to the file immediately."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cutoffs: dict[str, float] = self._load()

    def _load(self) -> dict[str, float]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # If it cannot be read, start empty — it is not worth blocking startup over, and the remaining risk
            # is only the tokens from the few hours that file covered. But it is not passed over silently.
            _log.exception("reading the token revocation list failed: %s", self._path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): float(v) for k, v in data.items()
                if isinstance(v, (int, float))}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._cutoffs, f)
        os.replace(tmp, self._path)

    def revoke(self, username: str, now: float | None = None) -> None:
        """Rejects every token issued to `username` so far."""
        now = time.time() if now is None else now
        with self._lock:
            self._cutoffs = {u: t for u, t in self._cutoffs.items()
                             if now - t < KEEP_SECONDS}
            self._cutoffs[username] = now
            self._save()

    def is_revoked(self, username: str, issued_at: float | None,
                   now: float | None = None) -> bool:
        """Whether this token was issued before the rejection cutoff.

        Tokens issued in the same second pass — `iat` is in seconds, so to avoid rejecting a token
        from logging in again right after a reset, the comparison must be truncated to seconds. If `iat`
        is missing, it is rejected as long as a record exists.
        """
        cutoff = self._cutoffs.get(username)
        if cutoff is None:
            return False
        now = time.time() if now is None else now
        if now - cutoff >= KEEP_SECONDS:
            return False
        return issued_at is None or issued_at < int(cutoff)
