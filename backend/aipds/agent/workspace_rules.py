# backend/aipds/agent/workspace_rules.py — reproduces the upstream AI-PLC
# layout in the workspace and prepends the language directive to it.
#
# The upstream (aws-samples/sample-ai-plc) Claude Code setup copies
# core-workflow.md to CLAUDE.md at the project root and keeps the detailed rules
# in aws-aiplc-rule-details/. Because core-workflow.md's `Rule details location:
# ./aws-aiplc-rule-details/` assumes a path relative to the CWD, the rules must
# live in the workspace, not in CLAUDE_CONFIG_DIR — only then does the agent read
# that path as written. This placement is also why the special handling of the
# `aiplc-rules/` prefix in file_read from the Strands days is no longer needed.
#
# Why the language directive lives **here** (spec 2026-08-03-bilingual-ko-en §3):
# CLAUDE_CONFIG_DIR is shared by every project, so it cannot hold a per-project
# language. In setting_sources=["user", "project"], "user" is that shared
# directory and "project" is the workspace, so a per-project language can only
# flow through the CLAUDE.md this file writes (= the project level).
#
# And the language directive **must not exist in two places.** Commit 7f33652 was
# that failure: core-workflow's "한국어로 진행" (proceed in Korean) and the
# template's `**CRITICAL**: ... exactly as defined` said opposite things, the
# latter won, and some 20 PR/FAQ questions stayed in English. So the language
# lines were removed from the upstream rule files and the shared config, and this
# module's LANGUAGE_DIRECTIVES became the single source (test_workspace_rules
# guards that invariant).
#
# **This file assembles only two things: the language directive + core-workflow.**
# At one point the tool-parameter encoding rule was also prepended (2026-08-16
# ws-a-v3), but that was a workaround for the AskUserQuestion path. Once that tool
# became denied by default (claude_driver.FILE_QUESTIONS_ENV) and the shared config
# stated "nothing below narrows it", the rationale was gone on both sides, so it
# was removed on 2026-08-18. The only case where the rule would be needed again is
# turning that env off to go back to the old question path, and even then the
# shared config's clause still applies as is.
from __future__ import annotations

import logging
from pathlib import Path

from aipds.pathsafe import stat_beneath, write_beneath

_log = logging.getLogger("aipds.agent")

_CORE_WORKFLOW = "aws-aiplc-rules/core-workflow.md"
_DETAILS_DIR = "aws-aiplc-rule-details"

#: The language convention prepended to the workspace `CLAUDE.md`. **Two complete
#: versions, one per language.**
#:
#: **Why not the ruleset tree (2026-08-18).** Up to 2047ac3 this was `language/`
#: inside the ruleset tree. Upstream `aiplc-rules/` contains only `.gitkeep`,
#: `aws-aiplc-rules/` and `aws-aiplc-rule-details/`, so it was our content mixed into
#: the upstream tree, and it kept a ruleset swap from being "replace the directory
#: wholesale" — doing that would make the directive disappear with it. And since we
#: treat that tree as read-only, it was not a place we could edit either.
#:
#: **Why code rather than a file (2026-08-19).** The only production reader was
#: `place_rules` below. What being a file bought us was a "may be missing" state and
#: one raise to guard it, whereas a constant cannot have that state — a string literal
#: cannot be lost, so "assemble without a directive" becomes structurally impossible.
#: `_LANGUAGES` is derived from this dict too, so there is nothing to keep in sync by
#: hand with the filesystem.
#:
#: And this is the repo's existing convention: text the model reads must follow the
#: project language, and code owns it as two per-language versions (`agent/prompts.py`,
#: `proto/prompts.py`, `survey/builder.py`, `survey/report_labels.py`, and the
#: `agent/discovery_guard.py` header writes it down as the convention).
#: `language/*.md` was the only exception.
#:
#: **Why not a single template with just the language name slotted in.** That fixes
#: the language of the prose to one side. That is a measured defect — on 2026-08-04 an
#: English project's conversation ran in Korean, and the only cause was that the
#: shared config file was Korean prose (that comment in discovery-config/CLAUDE.md:
#: "The language a document is written in is itself a language signal"). And implicit
#: signals alone are not enough either — `survey/builder.py` has a measurement in the
#: opposite direction (2026-08-05: putting a Korean spec into an English prompt made
#: every survey item come out in Korean; the closer, more specific signal wins). So we
#: do **both: name the language explicitly and write in that language.**
#:
#: **The two versions must be symmetric.** While they lived in two separate files they
#: drifted apart — ko was 3,389 characters / en 1,310, and en ended with "There is
#: nothing to translate", carrying no guidance on handling templates at all. English
#: projects need that guidance too (telling structural markers apart from text to
#: translate is language-independent, and templates can contain literals in another
#: language). test_workspace_rules guards the correspondence between the two versions.
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

#: Supported languages. Must be the same set as ProjectRegistry._LANGUAGES — it is
#: derived from the dict above, so there is no need to keep two places in sync by hand.
_LANGUAGES = tuple(LANGUAGE_DIRECTIVES)
_DEFAULT_LANGUAGE = "ko"


def _copy_if_changed(src: Path, ws: Path, rel: str) -> None:
    """Skip when size and mtime are **both** equal.

    This is a cache to avoid rewriting dozens of files every turn, but if the check
    is loose, the reason this module exists collapses on the spot. The point of
    placing the rules every turn is **for a ruleset swap to reach projects already
    in progress** (the rules are not in S3 and the workspace is rebuilt every turn).
    Comparing size alone means that when the ruleset is updated and some detail
    rule happens to have the same byte count, that one file stays stale with no
    signal at all — it only shows up as the agent following the old procedure, so
    it is nearly impossible to trace.

    Also checking mtime is enough: deployments replace rule files, so when the
    content changes the mtime changes too. A hash would require reading dozens of
    files every turn, bringing back the very cost this cache was meant to remove.
    The reason for `copy2` rather than `copyfile` is mtime preservation — without
    it dst gets a new timestamp every time, the comparison never matches, and the
    cache is effectively off.

    **Compare exactly with `st_mtime_ns`.** Truncating to seconds
    (`int(st_mtime)`) misses a rule replaced within the same second — common,
    since a deployment writes dozens of files in an instant. Nor do we relax it to
    "up to date if dst is newer than src": deploying by extracting an archive can
    restore the original mtime to the past, and then an updated rule would be
    judged stale forever. An exact match is precisely what "this is a copy of that
    file" means, and the cost of a false miss is just copying 23 small files one
    more time — not comparable to the cost of missing a change.

    **Links are not followed on the workspace side** (`pathsafe`). It is a tree the
    agent writes to, so `ws/rel` or one of its parent components could be a link
    pointing outside, and following it would overwrite whatever lies beyond with a
    rule file.
    """
    s = src.stat()
    d = stat_beneath(ws, rel)
    if d is not None and s.st_size == d.st_size and s.st_mtime_ns == d.st_mtime_ns:
        return
    write_beneath(ws, rel, src.read_bytes(), mtime_ns=s.st_mtime_ns)


def place_rules(workspace: str, rules_dir: str,
                language: str = _DEFAULT_LANGUAGE) -> None:
    """`LANGUAGE_DIRECTIVES[lang]` + `core-workflow.md` → `<workspace>/CLAUDE.md`,
    `aws-aiplc-rule-details/` → `<workspace>/aws-aiplc-rule-details/`.

    Idempotent and cheap to call every turn. Missing rules raise FileNotFoundError —
    proceeding silently would run the agent without knowing the workflow, which
    shows up as an empty conversation and is hard to trace. **The language
    directive has no such branch**: it is a constant, so it cannot be missing (a
    failure path that disappeared when it moved out of a file on 2026-08-19).

    **The language directive comes first.** In an earlier failure the template's
    CRITICAL, being "closer in context", beat the language directive, so here the
    language goes at the very top as the premise of the whole document, and both
    versions go as far as explaining how that CRITICAL should be read.

    An unknown language falls back to the default. The route validates it at
    creation time, so it cannot come in through the normal path, but running in
    Korean is better than running without rules because of a corrupted manifest.
    """
    root = Path(rules_dir)
    core = root / _CORE_WORKFLOW
    if not core.is_file():
        raise FileNotFoundError(f"AI-PLC core workflow not found: {core}")

    lang = language if language in _LANGUAGES else _DEFAULT_LANGUAGE
    if lang != language:
        _log.warning("unknown project language %r — using %s", language, lang)
    # The language directive comes from this module's constant, not `rules_dir`
    # (see LANGUAGE_DIRECTIVES). It used to be a file, which needed a "raise if
    # missing" branch; a constant cannot be missing, so that branch is gone.
    directive = LANGUAGE_DIRECTIVES[lang]

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    # The assembled result is not a source file, so _copy_if_changed's comparison
    # is not used. If the two language directives happened to have the same size
    # and mtime, switching languages would leave the file unchanged, and that
    # silence is exactly the failure shape this spec sets out to eliminate. Writing
    # one file is cheap. And since the same bytes are written every turn, the
    # prompt cache is preserved.
    #
    # **The language directive is line 1.** The tool-parameter encoding rule that
    # used to sit here was removed on 2026-08-18 — it was a workaround for the
    # AskUserQuestion path (file writes were clean and only that tool's input was
    # garbled; see agent/question_file_answers.py), and that tool is now denied by
    # default (claude_driver.FILE_QUESTIONS_ENV). The rule itself remains in the
    # shared config, and since that config states it does not narrow itself
    # (discovery-config/CLAUDE.md: "nothing below narrows it"), there is no longer a
    # reason to duplicate it here — when one rule lives in two places you cannot
    # tell which one is current, a principle this repository has already pinned
    # with tests for the depth bar.
    #
    # And this spot should go to what differs per project. The language directive
    # has lost a fight once (7f33652: the template's CRITICAL won and some 20 PR/FAQ
    # questions stayed in English). The premise of the whole document goes first.
    write_beneath(ws, "CLAUDE.md",
                  directive + "\n\n" + core.read_text(encoding="utf-8"))

    details = root / _DETAILS_DIR
    if not details.is_dir():
        # The workflow can start with core alone (detail rules are on demand) — warn only.
        _log.warning("AI-PLC rule details missing: %s", details)
        return
    for src in details.rglob("*"):
        if src.is_file():
            _copy_if_changed(src, ws, f"{_DETAILS_DIR}/{src.relative_to(details).as_posix()}")
