# backend/pathfinder/agent/workspace_rules.py — 상류 AI-PLC 레이아웃을
# 워크스페이스에 재현한다.
#
# 상류(aws-samples/sample-ai-plc)의 Claude Code 셋업은 core-workflow.md를
# 프로젝트 루트의 CLAUDE.md로 복사하고 상세 룰을 aws-aiplc-rule-details/에 둔다.
# core-workflow.md:18이 `Rule details location: ./aws-aiplc-rule-details/`로
# CWD 상대경로를 전제하므로 룰은 CLAUDE_CONFIG_DIR이 아니라 워크스페이스에 있어야
# 한다 — 그래야 에이전트가 그 경로를 그대로 읽는다. 이 배치가 있기 때문에
# Strands 시절 file_read의 `aiplc-rules/` 프리픽스 특수 처리가 필요 없어진다.
from __future__ import annotations

import logging
import shutil
from pathlib import Path

_log = logging.getLogger("pathfinder.agent")

_CORE_WORKFLOW = "aws-aiplc-rules/core-workflow.md"
_DETAILS_DIR = "aws-aiplc-rule-details"


def _copy_if_changed(src: Path, dst: Path) -> None:
    """크기가 같으면 건너뛴다. 룰은 읽기 전용이므로 크기 비교로 충분하고,
    매 턴 수십 개 파일을 다시 쓰지 않게 한다."""
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def place_rules(workspace: str, rules_dir: str) -> None:
    """`core-workflow.md` → `<workspace>/CLAUDE.md`,
    `aws-aiplc-rule-details/` → `<workspace>/aws-aiplc-rule-details/`.

    멱등이며 매 턴 호출해도 싸다. 룰이 없으면 FileNotFoundError — 조용히
    진행하면 에이전트가 워크플로우를 모르는 채로 돌고, 그건 빈 대화로 나타나서
    원인 추적이 어렵다.
    """
    root = Path(rules_dir)
    core = root / _CORE_WORKFLOW
    if not core.is_file():
        raise FileNotFoundError(f"AI-PLC core workflow not found: {core}")

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    _copy_if_changed(core, ws / "CLAUDE.md")

    details = root / _DETAILS_DIR
    if not details.is_dir():
        # core만으로도 워크플로우는 시작된다(상세 룰은 온디맨드) — 경고만.
        _log.warning("AI-PLC rule details missing: %s", details)
        return
    for src in details.rglob("*"):
        if src.is_file():
            _copy_if_changed(src, ws / _DETAILS_DIR / src.relative_to(details))
