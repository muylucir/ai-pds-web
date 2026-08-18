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
#
# **이 파일이 조립하는 것은 언어 지시 + core-workflow 둘뿐이다.** 한때 툴
# 파라미터 인코딩 규칙도 맨 앞에 붙였는데(2026-08-16 keumkang-v3), 그것은
# AskUserQuestion 경로의 워크어라운드였다. 그 도구가 기본 거부로 바뀌고
# (claude_driver.FILE_QUESTIONS_ENV) 공유 config가 "nothing below narrows it"을
# 명시하면서 근거가 양쪽에서 사라져 2026-08-18에 떼어냈다. 규칙이 필요해지는
# 유일한 경우는 그 env를 꺼서 옛 질문 경로로 돌아갈 때이고, 그때도 공유
# config의 조항이 그대로 적용된다.
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


def _copy_if_changed(src: Path, dst: Path) -> None:
    """크기와 mtime이 **둘 다** 같으면 건너뛴다.

    매 턴 수십 개 파일을 다시 쓰지 않기 위한 캐시이지만, 판정이 헐거우면 이
    모듈의 존재 이유가 그 자리에서 무너진다. 매 턴 배치하는 목적은 **룰셋 교체가
    진행 중인 프로젝트에 닿는 것**이다(룰은 S3에 없고 워크스페이스는 턴마다
    재구성된다). 크기만 비교하면 룰셋을 갱신했는데 어떤 상세 룰의 바이트 수가
    우연히 같을 때 그 파일만 낡은 채로 남고, 아무 신호가 없다 — 에이전트가 옛
    절차를 따르는 것으로만 드러나므로 추적이 거의 불가능하다.

    mtime을 함께 보는 것으로 충분하다: 룰은 배포가 파일을 갈아 끼우므로 내용이
    바뀌면 mtime도 바뀐다. 해시는 매 턴 수십 개 파일을 읽어야 해서 이 캐시가
    없애려던 비용을 되살린다. `copyfile`이 아니라 `copy2`인 이유는 mtime 보존이다 —
    보존하지 않으면 dst가 매번 새 시각을 갖고, 비교가 영원히 불일치해 캐시가
    사실상 꺼진다.

    **`st_mtime_ns`로 정확히 비교한다.** 초 단위로 자르면(`int(st_mtime)`) 같은
    초 안에 갈아 끼운 룰을 놓친다 — 배포는 파일 수십 개를 순식간에 쓰므로 흔한
    경우다. 그리고 "dst가 src보다 새로우면 최신"으로 완화하지도 않는다: 아카이브를
    풀어 배포하면 원본 mtime이 과거로 복원될 수 있고, 그러면 갱신된 룰이 영원히
    낡은 것으로 판정된다. 정확 일치가 "이것은 그 파일의 사본이다"의 정확한 의미고,
    빗나갈 때의 대가는 작은 파일 23개를 한 번 더 복사하는 것뿐이다 — 놓칠 때의
    대가와 비교가 되지 않는다.
    """
    if dst.is_file():
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and s.st_mtime_ns == d.st_mtime_ns:
            return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


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
    # 조립 결과는 원본 파일이 아니므로 _copy_if_changed의 비교를 쓰지 않는다.
    # 두 언어 지시의 크기·mtime이 우연히 같으면 언어를 바꿔도 파일이 그대로
    # 남는데, 그 침묵이 정확히 이 스펙이 없애려는 실패 모양이다. 파일 하나
    # 쓰기는 싸다. 그리고 매 턴 같은 바이트를 쓰므로 프롬프트 캐시는 유지된다.
    #
    # **언어 지시가 1행이다.** 여기 있던 툴 파라미터 인코딩 규칙은 2026-08-18에
    # 떼어냈다 — 그것은 AskUserQuestion 경로의 워크어라운드였고(파일 쓰기는
    # 깨끗하고 그 도구의 입력만 깨졌다, agent/question_file_answers.py 참고)
    # 그 도구는 이제 기본으로 거부된다(claude_driver.FILE_QUESTIONS_ENV).
    # 규칙 자체는 공유 config에 남아 있고, 그쪽이 스스로를 좁히지 않겠다고
    # 명시하므로(discovery-config/CLAUDE.md: "nothing below narrows it") 이 자리에
    # 복제해 둘 이유가 없어졌다 — 한 규칙이 두 곳에 있으면 어느 쪽이 최신인지
    # 알 수 없다는 것이 이 리포지토리가 깊이 기준에 대해 이미 테스트로 고정한
    # 원칙이다.
    #
    # 그리고 이 자리는 프로젝트마다 달라지는 것에 주어야 한다. 언어 지시는 한
    # 번 싸움에서 진 적이 있다(7f33652: 템플릿의 CRITICAL이 이겨 PR/FAQ 질문
    # 20여 개가 영어로 남았다). 문서 전체의 전제는 맨 앞에 둔다.
    (ws / "CLAUDE.md").write_text(
        directive.read_text(encoding="utf-8") + "\n\n"
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
