# backend/tests/parser_corpus.py — 파서 실측 코퍼스: 뒤섞기와 구조 요약.
#
# **왜 실측인가.** 파서 테스트의 픽스처가 사람이 손으로 쓴 "이상적인" 모양이던 동안,
# 실제 에이전트 산출물에서 파서가 조용히 실패했다 — 한국어 감사 로그 0건, 하위 단계가
# 스테이지로 섞인 사이드바, 사라진 `G)` 보기. 픽스처가 이상적이면 테스트는 통과하고
# 화면은 비어 있다. 그래서 실제 산출물을 코퍼스로 두고 모든 파서를 거기에 돌린다.
#
# **왜 뒤섞는가.** 리포는 공개되고 산출물에는 워크숍 참가자가 쓴 사업 내용이 담긴다.
# 여기서는 산문을 같은 길이의 무의미한 낱말로 바꾸되 파서가 읽는 **구조**는 그대로
# 둔다: 헤딩·목록 표시·펜스·보기 글자·타임스탬프·파서가 아는 라벨·스테이지 이름.
# 뒤섞은 파일의 구조 요약이 원본의 요약과 같다는 것을 `refresh`가 확인한 뒤에만
# 픽스처를 쓴다 — 뒤섞기가 구조를 건드렸다면 코퍼스가 거짓이 되기 때문이다.
#
# 갱신 방법(드릴 버킷에서 산출물을 받아 온 뒤):
#
#   aws s3 sync s3://<artifacts-bucket>/projects/ /tmp/corpus/raw \
#       --exclude '*' --include '*/aiplc-docs/audit.md' \
#       --include '*/aiplc-docs/aiplc-state.md' --include '*-questions.md'
#   cd backend && .venv/bin/python tests/parser_corpus.py /tmp/corpus/raw
#
# 원본은 리포에 들이지 않는다. 쓰이는 것은 `fixtures/real/` 아래의 뒤섞은 사본과
# `fixtures/real/summary.json`(구조 요약)뿐이다.
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

#: 뒤섞지 않는 낱말. 파서가 판정에 쓰는 것(스테이지·섹션 이름, 복수 선택·추천 표시,
#: 승인 문구)과 상류 룰이 정한 공개 용어뿐이다 — 프로젝트 고유의 말은 없어야 한다.
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

#: 이 어간을 **포함한** 한국어 낱말은 통째로 둔다. 파서가 부분 문자열로 판정한다 —
#: 복수 선택은 `"선택" in 괄호`로 본다(parsers/questions.py의 `_is_multi_select`) —
#: 그래서 `선택하세요`를 뒤섞으면 복수 선택 문항이 단일 선택으로 읽힌다.
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


#: 숫자는 구조일 때만 둔다 — 문항·단계 번호(`Question 3`, `Step 2`, `Q12`, `Part 1`)와
#: 줄 머리의 목록 번호. 산문의 숫자(고객사 수, 금액)는 식별 정보일 수 있다.
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
    # 타임스탬프 안의 `T`·`Z`는 한 글자라 그대로 남지만, 명시적으로 보호한다.
    parts, last = [], 0
    for m in _ISO.finditer(text):
        parts.append(both(text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(both(text[last:]))
    return "".join(parts)


def scrub(markdown: str) -> str:
    """산문을 뒤섞고 구조를 남긴다. 결정적이다 — 같은 입력은 같은 출력."""
    out = []
    for line in markdown.splitlines():
        m = _LABEL.match(line)
        if m and _family(m.group("label")):
            # 파서가 아는 라벨은 뜻이 곧 구조다 — 그대로 둔다.
            head = line[:m.end()]
            out.append(head + _scrub_words(line[m.end():]))
        else:
            out.append(_scrub_words(line))
    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def summarize(kind: str, name: str, markdown: str) -> dict:
    """파서 출력의 **구조**만 — 뒤섞기 전후가 같아야 하는 것."""
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
    """원본 산출물에서 뒤섞은 코퍼스와 구조 요약을 다시 만든다."""
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
