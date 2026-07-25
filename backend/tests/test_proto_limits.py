# backend/tests/test_proto_limits.py
from __future__ import annotations

from pathfinder.proto.limits import BuildSemaphore


def test_acquires_up_to_limit_then_refuses():
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True
    assert sem.try_acquire() is True
    assert sem.try_acquire() is False       # 3rd caller is refused, not queued
    assert sem.snapshot() == {"active_builds": 2, "max_builds": 2}


def test_release_frees_a_slot():
    sem = BuildSemaphore(max_concurrent=1)
    assert sem.try_acquire() is True
    assert sem.try_acquire() is False
    sem.release()
    assert sem.snapshot()["active_builds"] == 0
    assert sem.try_acquire() is True


def test_release_is_idempotent_and_never_goes_negative():
    """A session can be closed twice (explicit close + idle timeout racing),
    and each path releases. Over-releasing must not manufacture extra slots."""
    sem = BuildSemaphore(max_concurrent=1)
    sem.try_acquire()
    sem.release()
    sem.release()
    sem.release()
    assert sem.snapshot()["active_builds"] == 0
    assert sem.try_acquire() is True
    assert sem.try_acquire() is False       # still only ONE slot total


def test_zero_limit_refuses_everything():
    sem = BuildSemaphore(max_concurrent=0)
    assert sem.try_acquire() is False
    assert sem.snapshot() == {"active_builds": 0, "max_builds": 0}
