# backend/aipds/proto/limits.py — global cap on concurrent prototype
# builds.
#
# The MicroVM era had no cap: each build booted its own VM in Tokyo and AWS
# quota was the only ceiling. In-process builds share ONE box (the workshop
# EC2), where each session holds a `claude` subprocess (~300-500MB RSS) that
# may spawn a `next build` peaking around 2GB. So the ceiling is now ours to
# enforce.
#
# Deliberately NOT asyncio.Semaphore: that blocks the caller until a slot
# frees, which would leave the HTTP request hanging with no way to tell the
# user why. We refuse immediately (429 + a message naming the situation) --
# the user decision was "refuse, don't queue".
from __future__ import annotations


class BuildSemaphore:
    """Non-blocking counting gate. Single-threaded asyncio use only: every
    caller runs on the event loop and neither method awaits, so no lock is
    needed (the increment cannot be interleaved)."""

    def __init__(self, max_concurrent: int):
        self._max = max(0, int(max_concurrent))
        self._active = 0

    def try_acquire(self) -> bool:
        if self._active >= self._max:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        # Idempotent: close() and the idle timer can both fire for one
        # session, and clamping at 0 keeps an over-release from inventing a
        # slot that was never held.
        if self._active > 0:
            self._active -= 1

    def snapshot(self) -> dict[str, int]:
        return {"active_builds": self._active, "max_builds": self._max}
