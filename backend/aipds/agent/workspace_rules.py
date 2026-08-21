# backend/aipds/agent/workspace_rules.py -- reproduce the upstream AI-PLC layout
# in the workspace and prepend the language directive to it.
#
# Upstream's Claude Code setup (aws-samples/sample-ai-plc) copies core-workflow.md
# to CLAUDE.md at the project root and puts the detail rules in
# aws-aiplc-rule-details/. core-workflow.md's
# `Rule details location: ./aws-aiplc-rule-details/` assumes a CWD-relative path,
# so the rules have to live in the workspace rather than in CLAUDE_CONFIG_DIR --
# that is what lets the agent read that path verbatim. This layout is also why the
# Strands-era `aiplc-rules/` prefix special-case in file_read is no longer needed.
#
# Why the language directive lives **here** (spec 2026-08-03-bilingual-ko-en §3):
# CLAUDE_CONFIG_DIR is shared by every project, so it cannot carry a per-project
# language. With setting_sources=["user", "project"], "user" is that shared
# directory and "project" is the workspace -- so a per-project language can only
# flow through the CLAUDE.md this file writes (the project level).
#
# And the language directive **must not exist in two places.** Commit 7f33652 was
# that failure: core-workflow's "conduct everything in Korean" and the template's
# `**CRITICAL**: ... exactly as defined` said opposite things, the latter won, and
# some twenty PR/FAQ questions stayed in English. So the language line was removed
# from the upstream rule files and from the shared config, making this module's
# LANGUAGE_DIRECTIVES the single source (test_workspace_rules holds that
# invariant).
#
# **This file assembles exactly two things: the language directive and
# core-workflow.** It once also prepended the tool-parameter encoding rule
# (2026-08-16 keumkang-v3), which was a workaround for the AskUserQuestion path.
# That tool became denied by default (claude_driver.FILE_QUESTIONS_ENV) and the
# shared config started stating "nothing below narrows it", so the rationale
# disappeared from both ends and the rule was removed on 2026-08-18. The only case
# that would need it back is turning that env off to return to the old question
# path -- and the shared config's clause still applies there.
from __future__ import annotations

import logging
import shutil
from pathlib import Path

_log = logging.getLogger("aipds.agent")

_CORE_WORKFLOW = "aws-aiplc-rules/core-workflow.md"
_DETAILS_DIR = "aws-aiplc-rule-details"

#: The language convention prepended to the workspace `CLAUDE.md`. **Two complete
#: versions, one per language.**
#:
#: **Why not in the ruleset tree (2026-08-18).** Up to 2047ac3 this was
#: `language/` inside the ruleset tree. Upstream `aiplc-rules/` contains only
#: `.gitkeep`, `aws-aiplc-rules/` and `aws-aiplc-rule-details/`, so that was our
#: content mixed into an upstream tree -- and it stopped a ruleset swap from being
#: "replace the directory wholesale", because doing that would take the directive
#: with it. The tree is also treated as read-only, so it was a place we could not
#: edit the directive even when we needed to.
#:
#: **Why code rather than a file (2026-08-19).** The only production reader was
#: `place_rules` below. What being a file bought us was a "might be missing" state
#: plus one raise guarding it -- and a constant cannot have that state: a string
#: literal cannot be lost, so "assemble without the directive" becomes
#: structurally impossible. `_LANGUAGES` is derived from this dict too, so there is
#: nothing to keep in sync with the filesystem by hand.
#:
#: This is also the existing convention in this repo: text the model reads follows
#: the project language, and code owns it as two per-language versions
#: (`agent/prompts.py`, `proto/prompts.py`, `survey/builder.py`,
#: `survey/report_labels.py`, and `agent/discovery_guard.py`'s header states it as
#: the convention). `language/*.md` was the only exception.
#:
#: **Why not one template with the language name substituted in.** Then the prose
#: itself is fixed to one language, and that is a measured defect: on 2026-08-04 an
#: English project's conversation ran in Korean, and the only cause was that a
#: shared config file was Korean prose (see that comment in
#: discovery-config/CLAUDE.md: "The language a document is written in is itself a
#: language signal"). Implying it is not enough either -- `survey/builder.py` has
#: the measurement in the opposite direction (2026-08-05: an English prompt
#: carrying a Korean spec produced questions that were entirely Korean; the closer,
#: more concrete signal wins). So we do **both**: name the language AND write in
#: it.
#:
#: **The two versions must stay symmetric.** While they were two separate files
#: they drifted: ko 3,389 characters vs en 1,310, and the en one ended with "There
#: is nothing to translate" and so carried no judgement about handling the
#: templates at all. An English project needs that judgement too (telling
#: structural markers from translatable text is language-independent, and a
#: template can carry literals in another language). test_workspace_rules holds the
#: two versions against each other.
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

#: Supported languages. Must be the same set as ProjectRegistry._LANGUAGES --
#: derived from the dict above, so there is nothing to align by hand.
_LANGUAGES = tuple(LANGUAGE_DIRECTIVES)
_DEFAULT_LANGUAGE = "ko"


def _copy_if_changed(src: Path, dst: Path) -> None:
    """Skip only when size **and** mtime both match.

    This is a cache that avoids rewriting dozens of files every turn, but a loose
    test collapses this module's whole reason for existing. Placing the rules every
    turn is what makes **a ruleset swap reach a project already in progress** (the
    rules are not in S3, and the workspace is rebuilt each turn). Comparing size
    alone means that after a ruleset update, any detail rule whose byte count
    happens to match stays stale -- with no signal at all. It would surface only as
    the agent following an old procedure, which is close to untraceable.

    Size plus mtime is enough: rules are replaced by a deployment, so changed
    content also changes the mtime. Hashing would read dozens of files every turn
    and bring back the very cost this cache removes. `copy2` rather than `copyfile`
    is for mtime preservation -- without it dst gets a fresh timestamp every time,
    the comparison never matches, and the cache is effectively off.

    **Compare `st_mtime_ns` exactly.** Truncating to seconds (`int(st_mtime)`)
    misses rules replaced within the same second, which is common because a
    deployment writes dozens of files at once. Nor is this relaxed to "dst newer
    than src means current": unpacking an archive can restore the original mtime
    into the past, and then an updated rule is judged stale forever. Exact equality
    is the precise meaning of "this is a copy of that file", and the cost of
    guessing wrong is copying 23 small files one extra time -- no comparison to the
    cost of missing one.
    """
    if dst.is_file():
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and s.st_mtime_ns == d.st_mtime_ns:
            return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def place_rules(workspace: str, rules_dir: str,
                language: str = _DEFAULT_LANGUAGE) -> None:
    """`LANGUAGE_DIRECTIVES[lang]` + `core-workflow.md` -> `<workspace>/CLAUDE.md`,
    and `aws-aiplc-rule-details/` -> `<workspace>/aws-aiplc-rule-details/`.

    Idempotent and cheap enough to call every turn. Missing rules raise
    FileNotFoundError: proceeding quietly would leave the agent running without
    knowing the workflow, which shows up as an empty conversation and is hard to
    trace back. **The language directive has no such branch**: it is a constant, so
    it cannot be missing (a failure path that disappeared when it moved out of a
    file on 2026-08-19).

    **The language directive comes first.** In the earlier failure the template's
    CRITICAL won because it was "closer in context", so the language is placed at
    the very top as a premise for the whole document -- and both versions go on to
    explain how that CRITICAL should be read.

    An unknown language falls back to the default. The create route validates it,
    so nothing else arrives through the normal path; but running in Korean beats
    running without rules because of a corrupted manifest.
    """
    root = Path(rules_dir)
    core = root / _CORE_WORKFLOW
    if not core.is_file():
        raise FileNotFoundError(f"AI-PLC core workflow not found: {core}")

    lang = language if language in _LANGUAGES else _DEFAULT_LANGUAGE
    if lang != language:
        _log.warning("unknown project language %r — using %s", language, lang)
    # The language directive comes from this module's constant, not from
    # `rules_dir` (see LANGUAGE_DIRECTIVES). It used to be a file, which needed a
    # "raise if missing" branch; a constant cannot be missing, so that branch is
    # gone.
    directive = LANGUAGE_DIRECTIVES[lang]

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    # The assembled result is not a copy of a source file, so _copy_if_changed's
    # comparison does not apply. If the two language directives happened to share a
    # size and mtime, switching language would leave the file untouched -- and that
    # silence is exactly the failure shape this spec exists to remove. Writing one
    # file is cheap. The same bytes are written every turn, so the prompt cache
    # still holds.
    #
    # **The language directive is the only thing at the front now.** The
    # tool-parameter encoding rule that used to sit here was removed on
    # 2026-08-18: it was a workaround for the AskUserQuestion path (file writes
    # were clean; only that tool's input was mangled -- see
    # agent/question_file_answers.py), and that tool is now denied by default
    # (claude_driver.FILE_QUESTIONS_ENV). The rule itself still lives in the shared
    # config, which explicitly declines to narrow itself
    # (discovery-config/CLAUDE.md: "nothing below narrows it"), so there is no
    # reason to duplicate it here -- one rule in two places means nobody can tell
    # which copy is current, a principle this repository already pins with a test
    # for the depth bar.
    #
    # And this position should go to whatever varies per project. The language
    # directive has lost this fight once (7f33652: the template's CRITICAL won and
    # some twenty PR/FAQ questions stayed in English). A premise for the whole
    # document goes at the very top.
    (ws / "CLAUDE.md").write_text(
        directive + "\n\n"
        + core.read_text(encoding="utf-8"),
        encoding="utf-8")

    details = root / _DETAILS_DIR
    if not details.is_dir():
        # The workflow can start on core alone (detail rules are read on
        # demand), so this is a warning rather than an error.
        _log.warning("AI-PLC rule details missing: %s", details)
        return
    for src in details.rglob("*"):
        if src.is_file():
            _copy_if_changed(src, ws / _DETAILS_DIR / src.relative_to(details))
