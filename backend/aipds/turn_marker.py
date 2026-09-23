# backend/aipds/turn_marker.py — 도는 턴의 표식을 S3에 둔다.
#
# 턴 작업(turn_job.py)은 백엔드 프로세스 안에 있으므로 재시작을 넘지 못한다. 넘지
# 못하는 것 자체는 받아들인다 — 대신 **넘지 못했다는 사실**은 화면에 닿아야 한다.
# 그렇지 않으면 사용자는 끝나지 않는 "진행 중"이나 아무 설명 없는 빈 말풍선을 본다.
#
# 턴이 시작·종결될 때 이 표식을 쓴다. 기동 뒤 메모리에 턴이 없는데 표식이
# `running`이면 그 턴은 재시작으로 끊긴 것이다(`GET /turn`이 `interrupted`로 알린다).
# 종결 쓰기가 실패해 표식이 `running`으로 남아도 결과는 같다 — 메모리에 없는
# `running`은 어느 쪽이든 "이 프로세스가 끝까지 보지 못한 턴"이다.
from __future__ import annotations

import json
import logging

from aipds.s3store import S3StoreLike
from aipds.turn_job import TurnJob

_log = logging.getLogger(__name__)

#: 프로젝트 프리픽스는 S3Store가 붙인다. 워크스페이스 복원 대상
#: (runner._RESTORE_PREFIXES) 밖이라 에이전트 디렉터리에 나타나지 않는다.
TURN_MARKER_KEY = "turns/latest.json"


async def save_marker(s3: S3StoreLike, job: TurnJob) -> None:
    await s3.put(TURN_MARKER_KEY, json.dumps(
        {"turn_id": job.id, "kind": job.kind, "state": job.state}))


async def load_marker(s3: S3StoreLike) -> dict | None:
    """없거나 손상됐으면 None — 표식은 안내용이고, 없으면 "턴 없음"과 같다."""
    try:
        raw = await s3.get(TURN_MARKER_KEY)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("turn marker is not valid JSON — ignoring")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("turn_id"), str):
        return None
    return data
