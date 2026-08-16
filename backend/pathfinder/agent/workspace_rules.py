# backend/pathfinder/agent/workspace_rules.py — 상류 AI-PLC 레이아웃을
# 워크스페이스에 재현하고, 언어 지시를 그 앞에 붙인다.
#
# 상류(aws-samples/sample-ai-plc)의 Claude Code 셋업은 core-workflow.md를
# 프로젝트 루트의 CLAUDE.md로 복사하고 상세 룰을 aws-aiplc-rule-details/에 둔다.
# core-workflow.md의 `Rule details location: ./aws-aiplc-rule-details/`가
# CWD 상대경로를 전제하므로 룰은 CLAUDE_CONFIG_DIR이 아니라 워크스페이스에 있어야
# 한다 — 그래야 에이전트가 그 경로를 그대로 읽는다. 이 배치가 있기 때문에
# Strands 시절 file_read의 `aiplc-rules/` 프리픽스 특수 처리가 필요 없어진다.
#
# 언어 지시가 **여기**로 온 이유(스펙 2026-08-03-bilingual-ko-en §3):
# CLAUDE_CONFIG_DIR은 전 프로젝트가 공유하므로 프로젝트별 언어를 담을 수 없다.
# setting_sources=["user", "project"]에서 "user"가 그 공유 디렉토리이고
# "project"가 워크스페이스이므로, 프로젝트별 언어는 이 파일이 쓰는 CLAUDE.md
# (=project 레벨)로만 흐를 수 있다.
#
# 그리고 언어 지시는 **두 곳에 있으면 안 된다.** 커밋 7f33652가 그 실패였다:
# core-workflow의 "한국어로 진행"과 템플릿의 `**CRITICAL**: ... exactly as
# defined`가 반대를 말했고, 후자가 이겨서 PR/FAQ 질문 20여 개가 영어로 남았다.
# 그래서 상류 룰 파일과 공유 config에서 언어 줄을 지우고, 유일한 출처를
# language/{ko,en}.md로 만든다(test_workspace_rules가 그 불변식을 지킨다).
from __future__ import annotations

import logging
import shutil
from pathlib import Path

_log = logging.getLogger("pathfinder.agent")

_CORE_WORKFLOW = "aws-aiplc-rules/core-workflow.md"
_DETAILS_DIR = "aws-aiplc-rule-details"
_LANGUAGE_DIR = "language"

#: 지원 언어. ProjectRegistry._LANGUAGES와 같은 집합이어야 한다.
_LANGUAGES = ("ko", "en")
_DEFAULT_LANGUAGE = "ko"

#: 조립된 CLAUDE.md 맨 앞에 오는 툴 파라미터 인코딩 규칙.
#:
#: **왜 여기인가(2026-08-16 keumkang-v3의 결함).** 모델이 툴 파라미터의 한글을
#: `\uXXXX` 이스케이프로 쓰면서 hex를 오타내면 "유효하지만 틀린" 음절이 된다
#: (anthropics/claude-code#83033). 실측: 질문 파일은 `제공하시겠습니까`(U+ACA0)인데
#: 물어본 질문은 `제공하시겜습니까`(U+AC9C)였다. 사용자는 깨진 한국어를 보고,
#: 답변 되기록은 짝을 못 찾는다. 상류는 공식 미해결이고(모델 팀 이관, CLI로는
#: 복원 불가) 권고하는 유일한 완화책이 이 지시다.
#:
#: 지시는 `discovery-config/CLAUDE.md`에 이미 있었는데도 결함이 났다. 그 파일이
#: 스스로 "UI 접점에만 적용된다"며 모델을 **작업 디렉터리 CLAUDE.md**로 보내고,
#: 거기에는 조항이 없었기 때문이다. 그래서 모델이 실제로 지목받는 파일에 둔다.
#:
#: **언어 중립이어야 한다.** 한글이 섞이면 그 자체가 언어 신호가 되어 영어
#: 프로젝트의 대화를 한국어로 끌어당긴다(이 파일 상단의 7f33652 기록).
#: 인코딩 규칙은 어느 언어로 쓸지에 대해 아무 말도 하지 않는다.
_ENCODING_RULE = """<!-- pathfinder-tool-encoding -->
# Tool-parameter encoding (applies to every tool call, in any language)

Write non-ASCII text — Korean included — in tool-call parameters as **literal
UTF-8 characters**. Never as `\\uXXXX` unicode escapes.

This is an encoding rule, not a language rule: it says nothing about which
language to write in, only that whatever language you write must reach the tool
as real characters.

Why it is worth stating this bluntly: hand-spelling four hex digits per syllable
mis-spells some of them, and a mis-spelled codepoint decodes to a *different,
valid-looking* syllable. The question then reads as nonsense to the user and no
longer matches the question file it was written into, so their answer cannot be
recorded against it.
"""


def _copy_if_changed(src: Path, dst: Path) -> None:
    """크기가 같으면 건너뛴다. 룰은 읽기 전용이므로 크기 비교로 충분하고,
    매 턴 수십 개 파일을 다시 쓰지 않게 한다."""
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def place_rules(workspace: str, rules_dir: str,
                language: str = _DEFAULT_LANGUAGE) -> None:
    """`language/{lang}.md` + `core-workflow.md` → `<workspace>/CLAUDE.md`,
    `aws-aiplc-rule-details/` → `<workspace>/aws-aiplc-rule-details/`.

    멱등이며 매 턴 호출해도 싸다. 룰이 없으면 FileNotFoundError — 조용히
    진행하면 에이전트가 워크플로우를 모르는 채로 돌고, 그건 빈 대화로 나타나서
    원인 추적이 어렵다. 언어 지시가 없을 때도 같은 이유로 던진다: 그 실패는
    절반만 번역된 문서로 나타나 더 찾기 어렵다.

    **언어 지시가 앞에 온다.** 이전 실패에서 "맥락이 가까운" 템플릿의 CRITICAL이
    언어 지시를 이겼으므로, 여기서는 언어를 문서 전체의 전제로 맨 앞에 두고,
    ko.md가 그 CRITICAL을 어떻게 읽어야 하는지까지 설명한다.

    알 수 없는 language는 기본값으로 떨어진다. 라우트가 생성 시점에 검증하므로
    정상 경로로는 들어올 수 없지만, 손상된 매니페스트 때문에 룰 없이 도는
    것보다 한국어로 도는 편이 낫다.
    """
    root = Path(rules_dir)
    core = root / _CORE_WORKFLOW
    if not core.is_file():
        raise FileNotFoundError(f"AI-PLC core workflow not found: {core}")

    lang = language if language in _LANGUAGES else _DEFAULT_LANGUAGE
    if lang != language:
        _log.warning("unknown project language %r — using %s", language, lang)
    directive = root / _LANGUAGE_DIR / f"{lang}.md"
    if not directive.is_file():
        raise FileNotFoundError(f"language directive not found: {directive}")

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    # 조립 결과는 원본 파일이 아니므로 _copy_if_changed의 크기 비교를 쓰지
    # 않는다. 두 언어 지시의 크기가 우연히 같으면 언어를 바꿔도 파일이 그대로
    # 남는데, 그 침묵이 정확히 이 스펙이 없애려는 실패 모양이다. 파일 하나
    # 쓰기는 싸다.
    # 인코딩 규칙이 **맨 앞**이다: 출력 형식은 문서 전체의 전제이고, 언어 지시와
    # 경쟁하지 않는다(어느 언어로 쓸지에 대해 아무 말도 하지 않는다).
    (ws / "CLAUDE.md").write_text(
        _ENCODING_RULE + "\n"
        + directive.read_text(encoding="utf-8") + "\n\n"
        + core.read_text(encoding="utf-8"),
        encoding="utf-8")

    details = root / _DETAILS_DIR
    if not details.is_dir():
        # core만으로도 워크플로우는 시작된다(상세 룰은 온디맨드) — 경고만.
        _log.warning("AI-PLC rule details missing: %s", details)
        return
    for src in details.rglob("*"):
        if src.is_file():
            _copy_if_changed(src, ws / _DETAILS_DIR / src.relative_to(details))
