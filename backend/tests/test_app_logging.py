# backend/tests/test_app_logging.py — 애플리케이션 로그가 실제로 나가는지.
#
# 이 파일이 있는 이유는 진단 실패 한 건이다. 워크스페이스 채팅 내역이 복원되지
# 않는 버그를 쫓는 동안, 원인을 가리키는 로그가 하나도 없었다 — 코드에는
# `_log.info`/`_log.warning`이 있었지만 프로덕션 journald에 `pathfinder` 로거의
# 산출이 **0건**이었다. uvicorn은 자기 로거만 설정하고 루트에는 핸들러를 두지
# 않으므로, INFO는 조용히 사라지고 WARNING만 Python의 lastResort 핸들러로
# 새어나온다. 그래서 SDK가 내는 미러링 경고도, 우리 드라이버의 resume 판단
# 로그도 볼 수 없었다.
import logging

from aipds.app import configure_logging


def _reset(root: logging.Logger, saved):
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


def test_configure_logging_gives_the_root_logger_a_handler():
    """핸들러가 없으면 INFO는 어디로도 가지 않는다.

    lastResort는 WARNING 이상만, 그리고 포맷 없이 내보낸다 — 즉 없는 것과 같다.
    """
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        root.handlers[:] = []
        configure_logging()
        assert root.handlers, "루트에 핸들러가 없어 INFO 로그가 사라진다"
    finally:
        _reset(root, saved)


def test_pathfinder_and_sdk_loggers_emit_at_info(caplog):
    """우리 로거와 SDK 로거가 둘 다 INFO에서 잡혀야 한다.

    SDK 쪽이 함께 필요한 이유: 미러링 실패는 그쪽 로거로만 보고된다
    (`transcript_mirror_batcher`의 "dropping mirror frame" 경고). 그 경고가
    보이지 않아서 "프레임이 버려졌다"와 "프레임이 아예 오지 않았다"를 구별하는
    데 오래 걸렸다.
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
    """기동 경로가 두 번 불려도(테스트의 TestClient, reload) 핸들러가 쌓여
    같은 줄이 여러 번 찍히면 안 된다."""
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
