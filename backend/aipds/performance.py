from __future__ import annotations

import logging
import os
import time

PERFORMANCE_LOGS_ENV = "PATHFINDER_PERFORMANCE_LOGS"
_FALSY = frozenset({"0", "false", "no", "off"})


def performance_logs_enabled() -> bool:
    """Performance logs are enabled unless explicitly disabled."""
    value = os.environ.get(PERFORMANCE_LOGS_ENV, "true")
    return value.strip().lower() not in _FALSY


def log_performance(
    logger: logging.Logger,
    project_id: str | None,
    phase: str,
    started: float,
    **fields,
) -> None:
    if not performance_logs_enabled():
        return
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info(
        "turn_performance project_id=%s phase=%s duration_ms=%.1f%s",
        project_id or "unknown",
        phase,
        (time.perf_counter() - started) * 1000,
        f" {details}" if details else "",
    )
