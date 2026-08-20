# backend/aipds/agent/workspace_rules.py — 상류 AI-PLC 레이아웃을
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
# 이 모듈의 LANGUAGE_DIRECTIVES로 만든다(test_workspace_rules가 그 불변식을 지킨다).
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

_log = logging.getLogger("aipds.agent")

_CORE_WORKFLOW = "aws-aiplc-rules/core-workflow.md"
_DETAILS_DIR = "aws-aiplc-rule-details"

#: 워크스페이스 `CLAUDE.md` 맨 앞에 붙는 언어 규약. **언어별 완성본 두 벌이다.**
#:
#: **왜 룰셋 트리가 아닌가(2026-08-18).** 2047ac3까지는 `rule/aiplc-rules/language/`
#: 였다. 업스트림 `aiplc-rules/`에는 `.gitkeep`·`aws-aiplc-rules/`·
#: `aws-aiplc-rule-details/`뿐이므로 그것은 업스트림 트리 안에 섞인 우리 콘텐츠였고,
#: 룰셋 교체가 "디렉터리를 통째로 갈아 끼운다"로 끝날 수 없게 만들었다 — 그렇게 하면
#: 지시가 함께 사라진다. 읽기 전용으로 다루는 트리라 고칠 수도 없는 자리였다.
#:
#: **왜 파일이 아니라 코드인가(2026-08-19).** 프로덕션 독자는 아래 `place_rules`
#: 하나뿐이었다. 파일이라는 사실이 사 온 것은 "없을 수 있다"는 상태와 그것을 지키는
#: raise 하나였는데, 상수는 그 상태를 가질 수 없다 — 문자열 리터럴은 잃어버릴 수
#: 없으므로 "지시 없이 조립한다"가 구조적으로 불가능해진다. `_LANGUAGES`도 이 dict에서
#: 파생되므로 파일시스템과 손으로 맞출 것이 없다.
#:
#: 그리고 이것이 이 리포의 기존 규약이다: 모델이 읽는 텍스트는 프로젝트 언어를
#: 따라야 하고 코드가 언어별 두 벌로 소유한다(`agent/prompts.py`,
#: `proto/prompts.py`, `survey/builder.py`, `survey/report_labels.py`,
#: 그리고 `agent/discovery_guard.py` 헤더가 그것을 규약으로 적어 뒀다).
#: `language/*.md`만 예외였다.
#:
#: **왜 템플릿 한 벌에 언어 이름만 끼우지 않는가.** 그러면 산문의 언어가 한쪽으로
#: 고정된다. 그것은 실측된 결함이다 — 2026-08-04, 영어 프로젝트의 대화가 한국어로
#: 돌았고 원인은 공유 config 파일이 한국어 산문이었다는 것뿐이었다
#: (discovery-config/CLAUDE.md의 그 주석: "The language a document is written in is
#: itself a language signal"). 그리고 암시만으로도 부족하다 — `survey/builder.py`가
#: 반대 방향의 실측을 갖고 있다(2026-08-05: 영어 프롬프트에 한국어 명세를 실었더니
#: 문항이 전부 한국어로 나왔다, 더 가깝고 구체적인 신호가 이긴다). 그래서 **이름을
#: 명시하는 것과 그 언어로 쓰는 것을 둘 다** 한다.
#:
#: **두 판은 대칭이어야 한다.** 파일 두 개로 떨어져 있는 동안 갈라져 있었다 —
#: ko 3,389자 / en 1,310자였고, en은 "There is nothing to translate"로 끝내 양식
#: 처리 판단을 아예 담지 않았다. 영어 프로젝트에서도 그 판단은 필요하다(구조 마커와
#: 번역 대상의 구분은 언어와 무관하고, 양식에 다른 언어의 리터럴이 섞일 수 있다).
#: test_workspace_rules가 두 판의 대조를 지킨다.
LANGUAGE_DIRECTIVES = {
    "ko": """\
# 언어 규약 (이 문서 전체의 전제)

**모든 대화, 문서작성, 질의 응답은 한국어로 진행한다.** 단 기술용어·고유명사·
파일명·경로·도구 이름·코드 식별자는 영어를 그대로 유지한다.

**양식은 구조만 유지하고 사용자 노출 문구는 번역한다.** 이 문서 뒤의 워크플로우와
`aws-aiplc-rule-details/`의 양식에는 완성된 영어 문장이 리터럴로 박혀 있고, 그
바로 앞에 `**CRITICAL**: Use the ... format exactly as defined below. Do NOT
deviate from this structure.`가 있다. **그 CRITICAL이 요구하는 것은 구조다** —
섹션 순서, 항목 구성, 어느 질문이 들어가는지, 계층과 표기. 언어는 구조가 아니므로
질문 문구·헤딩·라벨·선택지는 한국어로 옮긴다. 질문을 빼거나 순서를 바꾸거나 새로
만들라는 뜻이 아니다.

## 분량은 감이 아니라 기준으로 맞춘다

<!-- depth-bar-language-clause -->

**분량을 감으로 조절하지 마라.** 토큰 비용이 언어마다 다르므로(한국어는 문자당
영어의 약 3배) "적당한 길이"라는 감각을 따르면 문서의 깊이가 과제가 아니라
**언어에 따라** 달라진다. 깊이 기준은 공유 config `CLAUDE.md`의
"Depth of what you write" 절이고, 그것을 이 언어 규약과 같은 무게로 읽어라.
""",
    "en": """\
# Language convention (a premise for this entire document)

**Conduct all conversation, document writing, and Q&A in English.** Keep file
names, paths, tool names, and code identifiers exactly as the rules spell them.

**Keep a template's structure; write its user-facing text in this language.** The
workflow below and the formats under `aws-aiplc-rule-details/` carry completed
sentences as literals, each preceded by `**CRITICAL**: Use the ... format exactly
as defined below. Do NOT deviate from this structure.` **What that CRITICAL
requires is the structure** — section order, which items appear, which questions
are asked, the heading levels and notation. Language is not structure, so question
wording, headings, labels and options are written in this language. It does not
mean dropping a question, reordering them, or inventing new ones.

## Calibrate length against a bar, not by feel

<!-- depth-bar-language-clause -->

**Do not calibrate length by feel.** The same content costs a different number of
tokens in different languages, so following a sense of "about the right length"
makes a document's depth track **the language** rather than the task. How deep to
write does not depend on the language: the shared config `CLAUDE.md` carries that
bar in its "Depth of what you write" section — read it with the same weight as
this convention.
""",
}

#: 지원 언어. ProjectRegistry._LANGUAGES와 같은 집합이어야 한다 — 위 dict에서
#: 파생되므로 두 곳을 손으로 맞출 일은 없다.
_LANGUAGES = tuple(LANGUAGE_DIRECTIVES)
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
    """`LANGUAGE_DIRECTIVES[lang]` + `core-workflow.md` → `<workspace>/CLAUDE.md`,
    `aws-aiplc-rule-details/` → `<workspace>/aws-aiplc-rule-details/`.

    멱등이며 매 턴 호출해도 싸다. 룰이 없으면 FileNotFoundError — 조용히
    진행하면 에이전트가 워크플로우를 모르는 채로 돌고, 그건 빈 대화로 나타나서
    원인 추적이 어렵다. **언어 지시에는 그 분기가 없다**: 상수이므로 없을 수
    없다(2026-08-19에 파일에서 옮겨 오면서 사라진 실패 경로다).

    **언어 지시가 앞에 온다.** 이전 실패에서 "맥락이 가까운" 템플릿의 CRITICAL이
    언어 지시를 이겼으므로, 여기서는 언어를 문서 전체의 전제로 맨 앞에 두고,
    두 판 모두 그 CRITICAL을 어떻게 읽어야 하는지까지 설명한다.

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
    # 언어 지시는 `rules_dir`가 아니라 이 모듈의 상수에서 온다
    # (LANGUAGE_DIRECTIVES 참고). 예전에는 파일이어서 "없으면 던진다" 분기가
    # 필요했는데, 상수는 없을 수 없으므로 그 분기가 사라졌다.
    directive = LANGUAGE_DIRECTIVES[lang]

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
        directive + "\n\n"
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
