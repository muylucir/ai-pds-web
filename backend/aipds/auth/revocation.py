# backend/aipds/auth/revocation.py — 관리자가 끊은 사용자의 토큰을 만료 전에 거부한다.
#
# 백엔드는 access 토큰을 로컬에서만 검증한다(서명·만료, auth/verifier.py). 그래서 관리자가
# 사용자를 비활성화·삭제·강등하거나 비밀번호를 재설정해도, 그 사용자가 이미 손에 든 access
# 토큰은 만료(ACCESS_TOKEN_VALIDITY_MINUTES, infra/lib/aipds-auth-stack.ts)까지 통과한다.
# Cognito의 GlobalSignOut도 이것을 바꾸지 못한다 — 로컬 검증은 Cognito에 묻지 않는다.
# refresh 쪽은 Cognito가 막는다: 비활성·삭제된 사용자는 refresh가 실패하고, 강등된 사용자는
# 새 그룹이 담긴 토큰을 받는다. 남는 구멍은 이미 발급된 access 토큰뿐이다.
#
# 그래서 사용자마다 "이 시각 이전에 발급된(`iat`) 토큰은 거부"를 기록한다. 기록은 토큰
# 수명만큼만 의미가 있으므로 그 뒤에 버린다. 인스턴스가 하나라 로컬 파일로 충분하고,
# 갱신이 백엔드를 재시작하므로 메모리만으로는 부족하다.
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

_log = logging.getLogger(__name__)

PATH_ENV = "AIPDS_REVOCATIONS_FILE"

#: 기록을 남기는 시간. access 토큰 수명(180분)보다 길게 — 그보다 오래된 토큰은 어차피
#: 만료로 거부된다.
KEEP_SECONDS = 4 * 3600


def default_path() -> Path:
    return Path(os.environ.get(PATH_ENV, "~/aipds-auth/revoked.json")).expanduser()


class Revocations:
    """username → 거부 기준 시각(epoch 초). 쓰기는 파일에 바로 남긴다."""

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
            # 읽지 못하면 비어 있는 것으로 시작한다 — 기동을 막을 일은 아니고, 남는 위험은
            # 그 파일이 담고 있던 몇 시간 안의 토큰뿐이다. 다만 조용히 넘기지 않는다.
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
        """`username`의 지금까지 발급된 토큰을 거부한다."""
        now = time.time() if now is None else now
        with self._lock:
            self._cutoffs = {u: t for u, t in self._cutoffs.items()
                             if now - t < KEEP_SECONDS}
            self._cutoffs[username] = now
            self._save()

    def is_revoked(self, username: str, issued_at: float | None,
                   now: float | None = None) -> bool:
        """이 토큰이 거부 기준 시각 이전에 발급됐는가.

        같은 초에 발급된 토큰은 통과시킨다 — `iat`는 초 단위라, 재설정 직후 다시 로그인한
        토큰을 거부하지 않으려면 초로 내려 비교해야 한다. `iat`가 없으면 기록이 있는 한
        거부한다.
        """
        cutoff = self._cutoffs.get(username)
        if cutoff is None:
            return False
        now = time.time() if now is None else now
        if now - cutoff >= KEEP_SECONDS:
            return False
        return issued_at is None or issued_at < int(cutoff)
