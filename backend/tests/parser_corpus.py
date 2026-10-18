# backend/tests/parser_corpus.py — real-world parser corpus: scrambling and structure summary.
#
# **Why real-world.** While the parser tests' fixtures were "ideal" shapes written by hand,
# the parsers failed silently on real agent artifacts — 0 entries from Korean audit logs, a
# sidebar with sub-steps mixed in as stages, a vanished `G)` option. When fixtures are ideal,
# the tests pass and the screen is empty. So real artifacts are kept as a corpus and every
# parser is run against it.
#
# **Why scramble.** The repo is public, and the artifacts contain business content written by
# workshop participants. Here the prose is replaced with meaningless words of the same length,
# while the **structure** the parsers read stays intact: headings, list markers, fences,
# option letters, timestamps, labels the parsers know, stage names. The fixtures are written
# only after `refresh` confirms that the scrambled file's structure summary equals the
# original's — if scrambling touched the structure, the corpus would be false.
#
# How to refresh (after pulling the artifacts down from the drill bucket):
#
#   aws s3 sync s3://<artifacts-bucket>/projects/ /tmp/corpus/raw \
#       --exclude '*' --include '*/aiplc-docs/audit.md' \
#       --include '*/aiplc-docs/aiplc-state.md' --include '*-questions.md'
#   cd backend && .venv/bin/python tests/parser_corpus.py /tmp/corpus/raw
#
# The originals never enter the repo. Only the scrambled copies under `fixtures/real/` and
# `fixtures/real/summary.json` (the structure summary) are used.
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from aipds.parsers.audit import _family, parse_audit_file
from aipds.parsers.questions import parse_question_file
from aipds.parsers.state import parse_state_file

CORPUS = Path(__file__).parent / "fixtures" / "real"

#: Words that are not scrambled. Only what the parsers use to decide (stage and section names,
#: multi-select and recommendation markers, approval phrases) and public terms defined by the
#: upstream rules — there must be no project-specific words here.
_KEEP = {w.lower() for w in """
Envision Solution Analysis Prototype Validation Product Strategy Go to Market
Workspace Detection Discovery Mode Selection Step Current Stage Progress Project Type
Greenfield Brownfield Question Answer Approved Approve PHASE Complete Completed Use Case
Intake Prioritization Context Generation Document PRFAQ PR FAQ Path Session Start GATE
Build Decision PROCEED Iterate Pivot Other recommended Timestamp User Input AI Response
Action Event Summary Result Raw COMPLETE RAW INPUT Answers Interpretation Time Date
select all that apply choose multiple only one pick
aiplc docs discovery envision md questions state audit
승인 추천 권고 기타 사용자 입력 답변 원문 해석 결정 선택 복수 중복 여러 모두 하나만 응답 요청 조치
요약 결과 시각 일시 맥락 및
""".split()}

#: Korean words that **contain** one of these stems are kept whole. The parsers decide by
#: substring — multi-select is detected with `"선택" in parentheses` (`_is_multi_select` in
#: parsers/questions.py) — so scrambling `선택하세요` would make a multi-select question read
#: as single-select.
_KEEP_STEMS = ("선택", "복수", "중복", "여러", "모두", "하나만", "한 개만", "승인", "추천", "권고")

_WORD = re.compile(r"[가-힣]+|[A-Za-z]+")
_HANGUL = "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허"
_LATIN = "abcdefghijklmnopqrstuvwxyz"
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.]+Z?")
_LABEL = re.compile(r"^(?P<lead>[ \t]*(?:[-*+][ \t]+)?\*\*)(?P<label>[^*\n]{1,80}?)(?P<close>\*\*)")


def _fake(word: str) -> str:
    digest = hashlib.sha256(word.encode("utf-8")).digest()
    if "가" <= word[0] <= "힣":
        return "".join(_HANGUL[digest[i % len(digest)] % len(_HANGUL)]
                       for i in range(len(word)))
    out = "".join(_LATIN[digest[i % len(digest)] % 26] for i in range(len(word)))
    return out.capitalize() if word[0].isupper() else out


#: Numbers are kept only when they are structure — question and step numbers (`Question 3`,
#: `Step 2`, `Q12`, `Part 1`) and list numbers at the start of a line. Numbers in prose
#: (customer counts, amounts) can be identifying information.
_STRUCTURAL_NUMBER = re.compile(r"(?:Question|Step|Part|Q|질문)\s*$|^\s*$")
_DIGITS = re.compile(r"\d+")


def _scrub_digits(text: str) -> str:
    def swap(m: re.Match) -> str:
        if _STRUCTURAL_NUMBER.search(text[:m.start()]):
            return m.group(0)
        digest = hashlib.sha256(m.group(0).encode()).digest()
        return "".join(str(digest[i] % 10) for i in range(len(m.group(0))))
    return _DIGITS.sub(swap, text)


def _scrub_words(text: str) -> str:
    def swap(m: re.Match) -> str:
        w = m.group(0)
        if len(w) == 1 or w.lower() in _KEEP or any(t in w for t in _KEEP_STEMS):
            return w
        return _fake(w)

    def both(chunk: str) -> str:
        return _scrub_digits(_WORD.sub(swap, chunk))
    # The `T` and `Z` inside a timestamp are single letters and would survive anyway,
    # but they are protected explicitly.
    parts, last = [], 0
    for m in _ISO.finditer(text):
        parts.append(both(text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(both(text[last:]))
    return "".join(parts)


def scrub(markdown: str) -> str:
    """Scramble the prose and keep the structure. Deterministic — the same input gives the same output."""
    out = []
    for line in markdown.splitlines():
        m = _LABEL.match(line)
        if m and _family(m.group("label")):
            # A label the parsers know means structure by definition — leave it as is.
            head = line[:m.end()]
            out.append(head + _scrub_words(line[m.end():]))
        else:
            out.append(_scrub_words(line))
    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def summarize(kind: str, name: str, markdown: str) -> dict:
    """Only the **structure** of the parser output — what must match before and after scrambling."""
    if kind == "audit":
        entries = parse_audit_file(markdown)
        return {"entries": len(entries),
                "with_timestamp": sum(1 for e in entries if e.timestamp),
                "with_input": sum(1 for e in entries if e.user_input.strip()),
                "with_response": sum(1 for e in entries if e.ai_response.strip())}
    if kind == "state":
        state = parse_state_file(markdown)
        return {"stages": [s.status for s in state.stages],
                "has_current": state.current_stage is not None}
    qfile = parse_question_file(name, markdown)
    return {"parse_ok": qfile.parse_ok,
            "questions": [{"n": q.number,
                           "options": [o.letter for o in q.options],
                           "multi": q.multi_select,
                           "answered": bool((q.answer or "").strip())}
                          for q in qfile.questions]}


def kind_of(path: Path) -> str:
    if path.name == "audit.md":
        return "audit"
    if path.name == "aiplc-state.md":
        return "state"
    return "questions"


def _language(project: Path) -> str:
    audit = project / "aiplc-docs" / "audit.md"
    text = audit.read_text(encoding="utf-8") if audit.exists() else ""
    hangul = len(re.findall(r"[가-힣]", text))
    return "ko" if hangul > len(text) * 0.05 else "en"


def refresh(raw_root: Path) -> None:
    """Rebuild the scrambled corpus and the structure summary from the original artifacts."""
    projects = sorted(p for p in raw_root.iterdir() if (p / "aiplc-docs").is_dir())
    counters: dict[str, int] = {}
    summary: dict[str, dict] = {}
    if CORPUS.exists():
        for old in CORPUS.rglob("*.md"):
            old.unlink()
    for project in projects:
        lang = _language(project)
        counters[lang] = counters.get(lang, 0) + 1
        alias = f"{lang}-{counters[lang]}"
        for src in sorted((project / "aiplc-docs").rglob("*.md")):
            kind = kind_of(src)
            if kind == "questions" and not src.name.endswith("-questions.md"):
                continue
            raw = src.read_text(encoding="utf-8")
            scrubbed = scrub(raw)
            before = summarize(kind, src.name, raw)
            after = summarize(kind, src.name, scrubbed)
            if before != after:
                raise SystemExit(f"scrubbing changed the structure of {src}:\n"
                                 f"  before {before}\n  after  {after}")
            rel = Path(alias) / src.relative_to(project / "aiplc-docs")
            dest = CORPUS / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(scrubbed, encoding="utf-8")
            summary[rel.as_posix()] = after
    (CORPUS / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote {len(summary)} files from {len(projects)} projects to {CORPUS}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: parser_corpus.py <raw projects dir>")
    refresh(Path(sys.argv[1]))
