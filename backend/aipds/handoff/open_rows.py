# backend/aipds/handoff/open_rows.py — vision.md's Open Questions table, collected and checked by code.
#
# **Why it exists.** When the model wrote the table itself it searched the whole material for "what nobody decided yet". That search
# has no edge, so it kept finding more — observed (ws-o): three generations from the same originals gave 21, 16 and 13 rows, 7 of
# the first 18 PM questions had been decided in Discovery, and carry-over kept them alive version after version. Sampling cannot
# be pinned (Opus 4.7 and later reject temperature), so the determinism has to come from the structure.
#
# **Collect, then classify.** Code collects candidates from the places that say on their own that something is open: the spec of
# record's open-questions section, the handoff tab's "unknown" and "being checked" answers, topics with no material, a spec that
# lags its change history, and the previous version's quote-backed rows whose quote is still there. The model classifies each one
# (who answers, when, how to build meanwhile) or closes it with a reason code checks. It may add a row only by quoting, verbatim,
# the passage that raises it; code checks that the quote is in that file. Code then renders the table — a candidate the model skips
# becomes a conservative row instead of vanishing, and no row appears without grounds.
#
# **Carry-over by key, not by the model.** Each row has a stable key. The next version reuses a row's wording by key, so the handoff
# tab's answers (keyed by sentence) stay attached, and re-offers a quote-backed row only while its quote is still in the material.
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Literal, cast

from pydantic import BaseModel

from aipds.handoff import supplement as supplement_mod
from aipds.handoff.readiness import Readiness

#: Where the record lives in the package prefix. Web-only, like the options — the harness reads the table in vision.md.
RECORD = "open-questions.json"

Kind = Literal["spec", "reconcile", "topic", "handoff", "quote"]
#: Why a candidate may leave the table. decided: the material or the ledger already settles it (cited) · answered: the PM
#: answered it on the handoff tab · duplicate: another candidate asks the same (named) · out_of_scope: it is only about a
#: feature outside the first release · not_product: a spec's open item about how the spec or its build record was written ·
#: source_changed: a quote-backed row whose quote is no longer in the material (set by code, never by the model).
Reason = Literal["decided", "answered", "duplicate", "out_of_scope", "not_product", "source_changed"]
_MODEL_REASONS = frozenset({"decided", "answered", "duplicate", "out_of_scope", "not_product"})
#: Which reasons each kind may be closed with. What the PM declared open on the handoff tab and a spec that lags its history are
#: facts, not judgments — only the PM or Discovery closes them.
_CLOSABLE: dict[str, frozenset[str]] = {
    "spec": frozenset({"decided", "answered", "duplicate", "out_of_scope", "not_product"}),
    "topic": frozenset({"decided", "answered"}),
    "quote": frozenset({"decided", "answered", "duplicate", "out_of_scope"}),
    "reconcile": frozenset(),
    "handoff": frozenset(),
}
#: A quote shorter than this proves nothing — a word or two is in every document.
_MIN_QUOTE_CHARS = 12
_MAX_QUESTION_CHARS = supplement_mod.MAX_QUESTION_CHARS


class Candidate(BaseModel):
    key: str
    kind: Kind
    #: What the source says — shown to the model, and the wording of last resort.
    text: str
    #: Source cell, in the citation form of vision rule 6 without brackets.
    source: str
    #: Fixed wording: a handoff answer's question, or the previous version's wording of this key.
    question: str = ""
    #: Fixed "until answered" — who a question is being checked with.
    until: str = ""
    #: Quote-backed rows: the evidence and the file (citation form) it is in.
    quote: str = ""
    file: str = ""


class Row(BaseModel):
    id: str
    key: str
    question: str
    who: str
    when: str
    until: str
    source: str
    quote: str = ""
    file: str = ""


class Closed(BaseModel):
    key: str
    #: The candidate's wording (or text) — what left the table.
    question: str
    reason: Reason
    by: str = ""


class Record(BaseModel):
    rows: list[Row] = []
    closed: list[Closed] = []
    #: Additions the model offered without a quote the material holds. Kept for the log; never shown as rows.
    rejected: list[str] = []


def cite(path: str) -> str:
    """Path used in citations — the relative path under `discovery/` that the originals use with each other."""
    return path.removeprefix("aiplc-docs/discovery/")


def grounded(source: str, known: set[str]) -> bool:
    """Whether the first path in a source is a file the reader has. Reads `path Q3`, `path · section` and `path, …`."""
    path = re.split(r"[\s,;·]", source.strip("`* "), maxsplit=1)[0].strip("`")
    for prefix in ("aiplc-docs/discovery/", "discovery/"):
        path = path.removeprefix(prefix)
    return path in known


def _norm(text: str) -> str:
    return " ".join(text.replace("**", "").replace("`", "").split())


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


# ---- Candidates ----

#: The open-questions section a spec writes for itself. The reconciliation pass (discovery-config/CLAUDE.md) says "list it under open
#: questions" and ko projects translate the heading, so both languages' usual words are read. A heading not on this list leaves its
#: items to quote-backed additions.
_SPEC_OPEN = re.compile(
    r"^(#{2,4})\s+(?:\d+[.)]\s*)?(미해결 질문|열린 질문|미결 사항|미해결 사항|"
    r"open questions|unresolved questions|open issues)\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s")
_ITEM = re.compile(r"^(?:\d+[.)]|[-*+])\s+(.+)$")


def spec_items(text: str) -> list[tuple[str, str]]:
    """(heading, item) for each top-level list item under a spec's open-questions section."""
    out: list[tuple[str, str]] = []
    level, heading, current = 0, "", ""
    for line in text.splitlines():
        head = _HEADING.match(line)
        if level and head and len(head.group(1)) <= level:
            break
        if not level:
            if found := _SPEC_OPEN.match(line.strip()):
                level, heading = len(found.group(1)), found.group(2)
            continue
        item = _ITEM.match(line)
        if item and not line.startswith((" ", "\t")):
            if current:
                out.append((heading, current))
            current = item.group(1).strip()
        elif current and line.strip():
            current += " " + line.strip()
    if current:
        out.append((heading, current))
    return out


def collect(readiness: Readiness, record: supplement_mod.Supplement, material: Mapping[str, str],
            previous: Record | None, lab: Mapping[str, str], topics: Mapping[str, str],
            ledger_handoff: str) -> tuple[list[Candidate], list[Closed]]:
    """Candidates in a fixed order, and what code closes on its own (answered on the handoff tab, quote gone from the material).

    `material` is vision's material (workspace path → text). `topics` words each supplementary topic in the project language.
    `ledger_handoff` is the ledger's handoff section title — the source of what the PM declared on the handoff tab.
    """
    candidates: list[Candidate] = []
    for p in readiness.prototypes:
        spec = cite(p.spec)
        for heading, item in spec_items(material.get(p.spec, "")):
            candidates.append(Candidate(key="spec:" + _hash(spec, _norm(item)), kind="spec",
                                        text=_norm(item), source=f"{spec} · {heading}"))
        if p.reconcile not in ("not_needed", "current") and p.history:
            candidates.append(Candidate(
                key="reconcile:" + spec, kind="reconcile", source=cite(p.history),
                text=("The spec of record does not reflect every change made after approval. The PM brings it up "
                      "to date with the change history in Discovery before the build.")))
    open_sections = {s.key for s in readiness.sections if s.status in ("partial", "missing")}
    for qid, section in supplement_mod.QUESTIONS:
        answer = record.answers.get(qid)
        if section in open_sections and (answer is None or answer.unknown):
            candidates.append(Candidate(key="topic:" + qid, kind="topic", text=topics.get(qid, qid),
                                        source=ledger_handoff if answer else lab["missing"]))
    declared: list[Candidate] = []
    for key, a in record.open_answers.items():
        if a.unknown or (a.pending_with and not a.text):
            declared.append(Candidate(
                key="handoff:" + key, kind="handoff", text=a.question, question=a.question,
                source=ledger_handoff,
                until="" if a.unknown else f"{lab['checking']}: {a.pending_with}"))
    closed: list[Closed] = []
    known = {cite(p): _norm(t) for p, t in material.items()}
    for row in (previous.rows if previous else []):
        if not row.quote:
            continue
        if _norm(row.quote) in known.get(row.file, ""):
            candidates.append(Candidate(key=row.key, kind="quote", text=row.quote, source=row.source,
                                        question=row.question, quote=row.quote, file=row.file))
        else:
            closed.append(Closed(key=row.key, question=row.question, reason="source_changed"))
    # The previous version's wording for the same key keeps the handoff tab's answers attached to their question.
    wording = {row.key: row.question for row in (previous.rows if previous else [])}
    for c in candidates:
        if not c.question and c.key in wording:
            c.question = wording[c.key]
    # A question the PM marked unknown or being checked is the same row as the candidate it came from: merge, keeping its until.
    by_question = {supplement_mod.open_key(c.question): c for c in candidates if c.question}
    for d in declared:
        same = by_question.get(supplement_mod.open_key(d.question))
        if same is not None:
            same.until = d.until or same.until
        else:
            candidates.append(d)
    # Answered on the handoff tab: closed here, not by the model. The answer goes into the body through the PM's answers.
    answered = {k for k, a in record.open_answers.items() if a.text and not a.unknown}
    kept: list[Candidate] = []
    for c in candidates:
        if c.question and supplement_mod.open_key(c.question) in answered:
            closed.append(Closed(key=c.key, question=c.question, reason="answered"))
        else:
            kept.append(c)
    return kept, closed


def prompt_block(candidates: Sequence[Candidate]) -> str:
    """The candidates as the vision prompt lists them, numbered C1…"""
    lines = []
    for i, c in enumerate(candidates, start=1):
        parts = [f"C{i} ({c.kind})", f"source: {c.source}", f"says: {c.text}"]
        if c.question:
            parts.append(f"wording (keep it): {c.question}")
        if c.until:
            parts.append(f"until answered (keep it): {c.until}")
        lines.append(" | ".join(parts))
    return "\n".join(lines) if lines else "(none)"


# ---- The model's reply ----

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def split_reply(text: str) -> tuple[dict, str]:
    """Model response → (open-questions JSON, the document text). An unreadable or missing JSON is `{}` — every candidate then
    becomes a conservative row, so a bad reply costs judgment, not candidates."""
    title = re.search(r"^# ", text, re.MULTILINE)
    head = text[:title.start()] if title else text
    body = text[title.start():] if title else ""
    match = _FENCE.search(head)
    if not match:
        return {}, body or text
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}, body
    return (data if isinstance(data, dict) else {}), body


def resolve(reply: Mapping, candidates: Sequence[Candidate], closed: Sequence[Closed],
            material: Mapping[str, str], ledger: str, lab: Mapping[str, str]) -> Record:
    """Checks the model's classification and builds the table's rows. Nothing the model says is taken without its grounds:
    a candidate it does not place stays as a conservative row, a closing reason that does not hold reopens it, and an addition whose
    quote is not in the named file is dropped."""
    texts = {cite(p): _norm(t) for p, t in material.items()}
    known = set(texts)
    by_cid = {f"C{i}": c for i, c in enumerate(candidates, start=1)}
    rows: list[Row] = []
    out_closed = list(closed)
    rejected: list[str] = []
    placed: set[str] = set()
    seen_keys: set[str] = set()
    taken_ids: set[str] = set()

    def add(row: Row) -> None:
        rows.append(row)
        seen_keys.add(row.key)
        taken_ids.add(row.id)

    for q in _items(reply, "questions"):
        cid = str(q.get("from") or "").strip()
        question = _one_line(q.get("question"))[:_MAX_QUESTION_CHARS]
        who, when, until = _classify(q, lab)
        rid = str(q.get("id") or "").strip()
        if cid:
            c = by_cid.get(cid)
            if c is None or cid in placed:
                continue
            placed.add(cid)
            until = c.until or until
            source = _one_line(q.get("source"))
            add(Row(id=rid, key=c.key, question=c.question or question or c.text[:_MAX_QUESTION_CHARS],
                    who=who, when=when, until=until,
                    source=source if source and grounded(source, known) else c.source,
                    quote=c.quote, file=c.file))
            continue
        raw = q.get("quote")
        evidence: dict = raw if isinstance(raw, dict) else {}
        file = cite(str(evidence.get("file") or "").removeprefix("discovery/").strip())
        quote = _one_line(evidence.get("text"))
        key = "quote:" + _hash(file, _norm(quote))
        if (not question or len(_norm(quote)) < _MIN_QUOTE_CHARS or file not in texts
                or _norm(quote) not in texts[file]):
            rejected.append(question or quote)
            continue
        if key in seen_keys:
            continue
        source = _one_line(q.get("source"))
        add(Row(id=rid, key=key, question=question, who=who, when=when, until=until,
                source=source if source and grounded(source, known) and source.startswith(file) else file,
                quote=quote, file=file))

    for x in _items(reply, "closed"):
        cid = str(x.get("from") or "").strip()
        c = by_cid.get(cid)
        reason = str(x.get("reason") or "").strip()
        by = _one_line(x.get("by"))
        if c is None or cid in placed or reason not in _MODEL_REASONS or reason not in _CLOSABLE[c.kind]:
            continue
        if reason == "decided" and not _settles(by, known, ledger):
            continue
        if reason == "duplicate":
            target = by_cid.get(by)
            if target is None or target.key not in seen_keys:
                continue
        placed.add(cid)
        out_closed.append(Closed(key=c.key, question=c.question or c.text, reason=cast(Reason, reason), by=by))

    for cid, c in by_cid.items():
        if cid in placed:
            continue
        # Not placed, or closed without grounds: a conservative row. Whoever reads it can still answer it.
        add(Row(id="", key=c.key, question=(c.question or c.text)[:_MAX_QUESTION_CHARS],
                who=lab["external"] if c.until else lab["pm"], when=lab["before"],
                until=c.until or "—", source=c.source, quote=c.quote, file=c.file))

    number = 0
    for row in rows:
        if re.fullmatch(r"O-\d{2,3}", row.id) and sum(r.id == row.id for r in rows) == 1:
            continue
        while True:
            number += 1
            candidate_id = f"O-{number:02d}"
            if candidate_id not in taken_ids:
                break
        taken_ids.add(candidate_id)
        row.id = candidate_id
    return Record(rows=rows, closed=out_closed, rejected=rejected)


def _items(reply: Mapping, name: str) -> list[dict]:
    value = reply.get(name)
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def _classify(q: Mapping, lab: Mapping[str, str]) -> tuple[str, str, str]:
    """Who answers, when, until — each held to its allowed values. Unknown values fall to the conservative side: the PM, before
    build. A during-build row with no way to build meanwhile is before build — the table's own rule."""
    who_given = _one_line(q.get("who")).strip("* ")
    who = next((v for v in (lab["external"], lab["dev"], lab["pm"]) if who_given.lower() == v.lower()), lab["pm"])
    when_given = _one_line(q.get("when")).strip("* ")
    when = lab["during"] if when_given.lower() == lab["during"].lower() else lab["before"]
    until = _one_line(q.get("until"))
    if when == lab["during"] and until in ("", "—", "-"):
        when = lab["before"]
    if when == lab["before"]:
        until = "—"
    return who, when, until


_QUESTION_FILE = re.compile(r"[\w./-]+-questions\.md")
_QUESTION_NO = re.compile(r"Q(\d+)")


def _refs(text: str) -> list[tuple[str, int]]:
    """Question rows a citation names: `go-to-market/gtm-questions.md Q12, Q13` → both."""
    hits = list(_QUESTION_FILE.finditer(text))
    out: list[tuple[str, int]] = []
    for i, hit in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        out += [(hit.group(0), int(n)) for n in _QUESTION_NO.findall(text[hit.end():end])]
    return out


def _settles(by: str, known: set[str], ledger: str) -> bool:
    """A "decided" closing must cite something the reader has: a file in the package, and for a ledger citation a row the
    ledger really has."""
    if not by or not grounded(by, known):
        return False
    return all(re.search(rf"{re.escape(path)} Q{number}\b", ledger) for path, number in _refs(by))


# ---- Rendering ----

def render(record: Record, lab: Mapping[str, str]) -> str:
    columns = lab["open_columns"]
    head = "| " + " | ".join(columns) + " |\n|" + "---|" * len(columns)
    body = "\n".join("| " + " | ".join(_cell(c) for c in (
        r.id, r.question, r.who, r.when, r.until or "—", r.source)) + " |" for r in record.rows)
    return head + ("\n" + body if body else "")


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


_OPEN_HEADING = re.compile(r"^(#{2,4})\s+Open Questions\b", re.IGNORECASE)


def insert(doc: str, table: str) -> str:
    """vision.md with its Open Questions section replaced by the table. Whatever the model wrote there goes — the table is code's."""
    lines = doc.rstrip("\n").splitlines()
    for i, line in enumerate(lines):
        if found := _OPEN_HEADING.match(line):
            level = len(found.group(1))
            end = next((j for j in range(i + 1, len(lines))
                        if (h := _HEADING.match(lines[j])) and len(h.group(1)) <= level), len(lines))
            tail = ["", *lines[end:]] if end < len(lines) else []
            return "\n".join([*lines[:i + 1], "", table, *tail]) + "\n"
    return "\n".join([*lines, "", "### Open Questions", "", table]) + "\n"


# ---- Marks for unconfirmed AI suggestions ----

_BRACKET = re.compile(r"\[[^\[\]]*\]")


def mark(doc: str, kept: set[tuple[str, int]], label: str, ledger_name: str) -> str:
    """Puts "(<label>)" before the first citation on a line that cites the ledger row of an accepted, unconfirmed AI suggestion,
    and removes the marks the model wrote. The instructions alone mismarked — a line marked without citing such a row, a row cited
    without the mark — and the ledger already says who decided each row."""
    mark_text = f"({label})"
    out: list[str] = []
    in_open = False
    for line in doc.split("\n"):
        if _HEADING.match(line):
            in_open = bool(_OPEN_HEADING.match(line))
            out.append(line)
            continue
        line = re.sub(rf"\s*{re.escape(mark_text)}", "", line)
        if not in_open:
            for bracket in _BRACKET.finditer(line):
                body = bracket.group(0)
                if ledger_name in body and any(ref in kept for ref in _refs(body)):
                    line = line[:bracket.start()].rstrip() + f" {mark_text} " + line[bracket.start():]
                    break
        out.append(line)
    return "\n".join(out)
