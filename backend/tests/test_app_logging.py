# backend/tests/test_app_logging.py — whether application logs actually get emitted.
#
# This file exists because of one diagnostic failure. While chasing a bug where workspace
# chat history was not restored, there was not a single log pointing to the cause — the code
# had `_log.info`/`_log.warning`, but production journald had **0** entries from the `aipds`
# logger. uvicorn configures only its own loggers and puts no handler on the root, so INFO
# silently disappears and only WARNING leaks out through Python's lastResort handler.
# As a result, neither the SDK's mirroring warnings nor our driver's resume-decision
# logs were visible.
import logging

from aipds.app import configure_logging


def _reset(root: logging.Logger, saved):
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


def test_configure_logging_gives_the_root_logger_a_handler():
    """Without a handler, INFO goes nowhere.

    lastResort emits only WARNING and above, and without formatting — i.e., as good as nothing.
    """
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        root.handlers[:] = []
        configure_logging()
        assert root.handlers, "루트에 핸들러가 없어 INFO 로그가 사라진다"
    finally:
        _reset(root, saved)


def test_aipds_and_sdk_loggers_emit_at_info(caplog):
    """Both our loggers and the SDK loggers must be captured at INFO.

    Why the SDK side is needed too: mirroring failures are reported only through its logger
    (the "dropping mirror frame" warning from `transcript_mirror_batcher`). Because that warning
    was not visible, it took a long time to tell "frames were dropped" apart from "frames
    never arrived at all".
    """
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging()
        with caplog.at_level(logging.INFO):
            logging.getLogger("aipds.agent").info("OURS")
            logging.getLogger(
                "claude_agent_sdk._internal.transcript_mirror_batcher"
            ).warning("THEIRS")
        assert "OURS" in caplog.text
        assert "THEIRS" in caplog.text
    finally:
        _reset(root, saved)


def test_configure_logging_is_idempotent():
    """Even if the startup path is called twice (the test TestClient, reload), handlers must not
    pile up and print the same line multiple times."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        root.handlers[:] = []
        configure_logging()
        first = len(root.handlers)
        configure_logging()
        assert len(root.handlers) == first
    finally:
        _reset(root, saved)
