# backend/pathfinder/workspace_sync.py — 워크스페이스 파일을 정본(S3)에 올리는 규칙.
#
# **왜 이 모듈이 생겼는가(2026-08-18).** 화면에 "문서가 작성되었다"고 나오는데 문서
# 탭 드롭다운에 없고, 목록에 보여서 골라도 내용이 없고, 잠깐 나타났다 사라지고,
# "문서 리뷰" 화면에서는 보였다. 증상 넷이 한 원인이었다:
#
#     `file_changed`는 **로컬** 쓰기에서 즉시 알리는데, 정본 게시는 **턴 종료**까지
#     미뤄졌다. UI의 읽기 경로는 전부 S3다(runner.read_file / list_files).
#
# 실측이 그대로 보였다: 한 프로젝트의 `aiplc-docs` 16개 파일의 S3 타임스탬프가 전부
# 같은 1초 안이었다 — 턴 중에는 아무것도 없고 끝에 몰아서 올라간다. 그래서 90초
# 넘게 도는 턴에서 사용자는 "썼다"는 말만 보고 문서를 볼 수 없었다.
#
# 프론트는 이미 이것을 막으려 애쓰고 있었다(WorkspaceDocPanel이 현재 path를 목록에
# union하고 404를 "미동기화"로 구분한다). 그러나 읽을 것이 정본에 없으면 프론트가
# 할 수 있는 것이 없다 — 그래서 남은 증상이 위 넷이다.
#
# 계약은 질문 파일에 이미 적용한 것과 같다(a2b9623의 "카드를 광고하기 전에 파일을
# S3에 올린다"): **광고하기 전에 게시한다.**
#
# **이 모듈이 규칙을 소유하는 이유.** 올리는 곳이 둘이 됐다 — 턴 종료 배치
# sync(`runner._sync_workspace_to_s3`)와 쓰기 직후 게시(`claude_driver`의
# PostToolUse 훅). `audit.md` 리댁션과 대상 글롭이 두 곳에 복사되면 한쪽만 고쳐져
# **audit.md가 리댁션 없이 정본에 올라가는** 경로가 생긴다. 그 부류의 갈라짐은
# 에러를 내지 않는다.
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from pathfinder.globmatch import matches_glob
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.pathsafe import reject_unsafe
from pathfinder.s3store import S3StoreLike

_log = logging.getLogger("pathfinder.workspace")

#: 정본으로 올리는 서브트리. `AgentRunner._SYNC_GLOBS`가 이 값을 쓴다 — 두 벌로
#: 두면 쓰기 직후 게시와 턴 종료 배치가 다른 집합을 올리고, 그 어긋남은 "있다가
#: 없어지는 문서"로 보인다.
SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*", "uploads/**/*")

#: 저장 시 리댁션하는 키. `audit.md`는 도구 출력을 원문으로 담고, S3를 직접 읽는
#: 사람에게도 노출되므로 올릴 때 자격증명을 지운다. 산출물 문서는 그대로 올린다.
_REDACTED_KEYS = frozenset({"aiplc-docs/audit.md"})


def is_synced_key(key: str) -> bool:
    """이 키가 정본으로 올라가는 집합에 속하는가."""
    return any(matches_glob(key, glob) for glob in SYNC_GLOBS)


def content_for_s3(key: str, text: str) -> str:
    """정본에 올릴 내용. `audit.md`만 리댁션한다."""
    return redact_credentials(text) if key in _REDACTED_KEYS else text


async def publish_file(
    s3: S3StoreLike,
    local_root: Path,
    key: str,
    on_published: Callable[[str, str, str | None], None] | None = None,
) -> bool:
    """워크스페이스의 파일 하나를 정본에 올린다. 올렸으면 True.

    **예외를 던지지 않는다.** 이 함수는 쓰기 직후 훅에서 불린다 — 게시는 부수
    동작이고, 파일이 이미 지워졌거나 S3가 잠깐 실패했다고 턴을 죽이면 안 된다.
    턴 종료 배치 sync가 여전히 백스톱이다(runner의 done/error 경로와 `finally`).

    안전하지 않은 키는 거부한다(fail-closed). 배치 sync는 그런 키를 만나면 sync
    전체를 멈추고, 여기서는 그 한 파일을 거부한다 — 훅에서 turn을 멈추는 것보다
    거부가 맞다.
    """
    try:
        reject_unsafe(key)
    except Exception:
        _log.warning("refusing to publish an unsafe key: %r", key)
        return False
    if not is_synced_key(key):
        return False
    path = local_root / key
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # 이미 지워졌거나 디렉터리다 — 배치 sync가 최종 상태를 올린다.
        return False
    canonical = content_for_s3(key, text)
    try:
        etag = await s3.put(key, canonical)
    except Exception:
        _log.exception("publishing %s to S3 failed — the turn-end sync will "
                       "retry", key)
        return False
    if on_published is not None:
        on_published(key, canonical, etag)
    return True
