# backend/aipds/transcript_restore.py — 재개할 트랜스크립트를 프로젝트별 config dir에 되살린다.
#
# **왜 SDK의 복원을 쓰지 않는가.** SDK는 `session_store` + `resume`이면 스토어의 트랜스크립트를
# 백엔드의 `/tmp/claude-resume-*`에 풀고 CLI의 `CLAUDE_CONFIG_DIR`을 **그 임시 디렉터리로
# 바꾼다**(`_internal/session_resume.py`). 거기로 복사되는 것은 `.credentials.json`·
# `.claude.json`·`settings.json`뿐이라 재개된 CLI에는 CLAUDE.md도 스킬도 없다 — 실측
# (2026-09-23): 재개된 CLI에 스킬 목록과 CLAUDE.md의 코드워드를 물으면 "Skills: None,
# CLAUDE.md is no longer present". Discovery는 상주 프로세스라 재연결 뒤에만, 프로토타입 수정
# 빌드는 매번 그 경로를 탔다. 실행 래퍼(aipds/launcher.py) 아래에서는 더 나쁘다: 다른 uid에
# PrivateTmp인 CLI가 그 임시 디렉터리를 아예 못 본다.
#
# 그래서 역할을 나눈다:
# - 로컬(`<config>/projects/…`)이 CLI의 작업 사본이다. CLI가 직접 쓰고 직접 재개한다.
# - S3는 미러다. SDK에는 `load`가 비어 있는 `MirrorOnlyStore`를 넘겨 SDK가 임시 디렉터리로
#   풀지 않게 하고, 쓰기(append)만 그대로 흘린다.
# - 로컬 사본이 없을 때(인스턴스 교체 직후, 에이전트 홈 첫 사용, CLI의 cleanupPeriodDays
#   청소 뒤)만 `restore`가 S3에서 받아 로컬에 둔다.
#
# `restore`는 SDK의 **비공개** `materialize_resume_session`을 빌려 쓴다 — 스토어 형식(서브에이전트
# 트랜스크립트, 메타데이터 사이드카, 줄 순서)을 우리가 다시 구현하면 SDK와 어긋난다. SDK가 그
# 함수를 옮기면 경고를 남기고 새 세션으로 떨어진다(= 지금까지 인스턴스 교체 뒤의 동작).
# tests/test_transcript_restore.py가 그 계약을 실제 SDK로 고정한다.
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

_log = logging.getLogger("aipds.agent")


class MirrorOnlyStore:
    """쓰기는 그대로, 읽기는 없는 척. SDK가 재개 때 임시 config dir을 만들지 않게 한다.

    SDK는 `load`가 비면 "스토어에 없음"으로 보고 `--resume <id>`를 CLI에 그대로 넘긴다 —
    CLI는 로컬 사본에서 재개한다. `append` 말고 SDK가 라이브 세션에서 부르는 것은 없다
    (`list_sessions`는 continue_conversation에서만, 이 앱은 쓰지 않는다).
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def append(self, key: Any, entries: list) -> None:
        await self.inner.append(key, entries)

    async def load(self, key: Any) -> None:
        return None


def _materializer():
    try:
        from claude_agent_sdk._internal.session_resume import materialize_resume_session
    except ImportError:
        return None
    return materialize_resume_session


async def restore(store: Any, config_dir: str | Path, cwd: str,
                  session_id: str) -> bool:
    """`store`의 `session_id` 트랜스크립트를 `config_dir/projects/` 아래에 쓴다.

    되살렸으면 True. 스토어에 없거나(새 프로젝트) 복원 수단이 없으면 False — 호출자는 새
    세션으로 시작한다. 이미 있는 로컬 파일은 덮지 않는다(로컬이 미러보다 새로울 수 있다:
    미러 쓰기는 턴 경계에서 모아서 나간다).
    """
    materialize = _materializer()
    if materialize is None:
        _log.warning("SDK has no materialize_resume_session — cannot restore "
                     "transcript %s; starting a fresh session", session_id)
        return False
    from claude_agent_sdk import ClaudeAgentOptions
    options = ClaudeAgentOptions(cwd=cwd, resume=session_id, session_store=store,
                                 env={"CLAUDE_CONFIG_DIR": str(config_dir)})
    materialized = await materialize(options)
    if materialized is None:
        return False
    try:
        src = Path(materialized.config_dir) / "projects"
        dst = Path(config_dir) / "projects"
        await asyncio.to_thread(_merge_tree, src, dst)
    finally:
        await materialized.cleanup()
    _log.info("restored transcript %s from the mirror into %s", session_id, dst)
    return True


def _merge_tree(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        target = dst / path.relative_to(src)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            # 임시 사본은 0600이다(SDK). 그룹이 읽어야 실행 래퍼 아래의 CLI가 연다.
            shutil.copyfile(path, target)
