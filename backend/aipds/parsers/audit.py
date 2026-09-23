# backend/aipds/parsers/audit.py
from __future__ import annotations
import logging
import re
from aipds.models import AuditEntry
from aipds.parsers.redaction import redact_credentials

_log = logging.getLogger(__name__)

# Any level-2 heading starts a candidate entry. The rules tell the agent to log
# under a SEMANTIC heading — "## Session Start", "## 최종 승인",
# "## Discovery Mode Selection" (core-workflow.md "Audit Logging" plus the
# per-stage examples) — and NEVER "## Entry N:". `## Entry N:` is still honored
# for pilot logs, and its explicit number is preserved.
_ENTRY = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LEGACY_ENTRY_NAME = re.compile(r"^Entry\s+(\d+)\s*:")

# **구조로 읽는다 — 라벨 목록을 맞추지 않는다.** 상류 룰의 예시는 `**Timestamp**:`,
# `**User Input**:`, `**AI Response**:`, `**Context**:` 넷이지만, 에이전트가 실제로
# 쓰는 라벨은 프로젝트마다 다르다(드릴 버킷 실측, 5개 프로젝트):
#
#   **User Input (COMPLETE RAW INPUT)** — 세션 앞부분에서 제공된 원본 요청:
#   **사용자 원문 입력 (raw input, 그대로 보존):**      ← 콜론이 굵게 안에 있다
#   - **Event**: ... / - **Answer**: ...               ← 목록 항목 라벨
#   **AI Action**: / **AI 해석:** / **답변 해석:**
#   ## 2026-08-19T08:00:19Z — 워크플로우 시작           ← 타임스탬프가 헤딩에 있다
#
# 네 라벨만 정확히 맞추던 동안 한국어 프로젝트의 감사 로그는 38개 항목 중 0개, 영어
# 프로젝트도 16개 중 2개가 읽혔다 — 리뷰 화면의 검증 요약·게이트 이력과 대시보드
# 활동 피드가 비었다. 템플릿 라벨은 ko 프로젝트에서 번역되고, 번역이 아니어도 모델이
# 꾸민다. 그래서 여기서는 "굵은 라벨 + 콜론"이라는 **모양**을 라벨로 보고, 라벨의
# **뜻**을 넓은 족(family)으로 분류한다.
#
# 라벨: 줄 머리(목록 표시 허용)의 `**X**`로, 콜론이 굵게 안(`**X:**`)이나 밖
# (`**X**:`)에 있거나, 뒤에 설명이 붙고 콜론으로 끝나거나(`**X** — 설명:`), 줄에
# 그것만 있는 경우다. 문장 안의 강조(`**차별성은 P4·P5에 있다.**`)는 콜론이 없어
# 라벨이 아니다.
_LABEL_LINE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?\*\*(?P<label>[^*\n]{1,80}?)\*\*(?P<rest>[^\n]*)$")
_DESCRIPTOR = re.compile(r"^\s*[—–-][^\n]*:\s*$")
# 한 줄에 네 표준 라벨을 몰아 쓴 파일럿 로그가 있다("\n"을 글자로 쓴 경우). 그런
# 줄은 라벨마다 줄을 나눠 위 규칙으로 읽는다.
_INLINE_CANONICAL = re.compile(r"\*\*(?:Timestamp|User Input|AI Response|Context)\*\*:")

# 라벨의 족. 괄호 주석과 끝 콜론을 떼고 소문자로 비교한다. 순서가 곧 우선순위다 —
# AI 쪽 라벨이 여럿이면(`AI Response`와 `AI Action`이 함께) 앞의 것을 쓴다.
_FAMILIES: list[tuple[str, re.Pattern]] = [
    ("timestamp", re.compile(r"^(timestamp|time|date|시각|시간|일시|타임스탬프)$")),
    ("context", re.compile(r"^(context|맥락|컨텍스트)$")),
    ("user_input", re.compile(
        r"^(user\s+(raw\s+)?(input|request|answers?|reply)|raw\s+input|answers?"
        r"|사용자.*(입력|답변|요청|응답|원문|선택)|요청\s*원문|원문\s*(입력|답변))$")),
    ("ai_response:0", re.compile(r"^(ai\s+response|ai\s*응답|response|응답)$")),
    ("ai_response:1", re.compile(r"^(ai\s+action|action|ai\s*조치|조치)$")),
    ("ai_response:2", re.compile(
        r"^(ai\s*해석|답변\s*해석|해석(\s*및\s*결정)?|interpretation|decision|결정)$")),
    ("ai_response:3", re.compile(r"^(event|summary|result|요약|결과)$")),
]
_PAREN_NOTE = re.compile(r"\s*[(（][^)）]*[)）]")

# 헤딩 머리의 타임스탬프: `## 2026-08-19T08:00:19Z — 제목`.
_HEADING_STAMP = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)\s*(?:[—–-]\s*)?(?P<title>.*)$")

# Real agent-written logs also use a SUB-HEADING + fenced block for the raw
# input instead of a label, e.g.
#     ### 사용자 입력
#     ```
#     승인
#     ```
# and then narrate the AI's side as the prose/bullets that follow.
_SUBHEAD_INPUT = re.compile(
    r"^###\s+(?:사용자\s*(?:입력|답변)|User\s+Raw\s+Input)[^\n]*\n+"
    r"(?:```[^\n]*\n(?P<fenced>.*?)```|(?P<plain>(?:(?!^#)[^\n]*\n?)*))",
    re.MULTILINE | re.DOTALL,
)
_STOP = re.compile(r"^(#{1,6}\s|---\s*$)")


def _family(label: str) -> str | None:
    norm = _PAREN_NOTE.sub("", label).strip().rstrip(":：").strip().lower()
    norm = re.sub(r"\s+", " ", norm)
    for family, pattern in _FAMILIES:
        if pattern.match(norm):
            return family
    return None


def _split_inline_markers(block: str) -> str:
    lines = []
    for line in block.splitlines():
        if len(_INLINE_CANONICAL.findall(line)) > 1:
            parts = _INLINE_CANONICAL.split(line)
            marks = _INLINE_CANONICAL.findall(line)
            if parts[0].strip():
                lines.append(parts[0].rstrip())
            lines.extend(f"{m} {p.strip()}" for m, p in zip(marks, parts[1:]))
        else:
            lines.append(line)
    return "\n".join(lines)


def _label_at(line: str) -> tuple[str, str] | None:
    """이 줄이 라벨이면 (족, 같은 줄의 값). 라벨이 아니거나 모르는 족이면 None."""
    m = _LABEL_LINE.match(line)
    if not m:
        return None
    label, rest = m.group("label"), m.group("rest")
    if label.rstrip().endswith((":", "：")):
        value = rest
    elif rest.lstrip().startswith((":", "：")):
        value = rest.lstrip()[1:]
    elif _DESCRIPTOR.match(rest) or not rest.strip():
        value = ""
    else:
        return None
    family = _family(label)
    return (family, value.strip()) if family else ("other", value.strip())


def _clean(text: str) -> str:
    """값에서 펜스·인용 표시·감싼 따옴표를 떼어 사람이 읽는 원문만 남긴다."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
    lines = [re.sub(r"^\s*>\s?", "", ln) for ln in lines]
    s = "\n".join(lines).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'“”":
        s = s[1:-1]
    elif len(s) >= 2 and s[0] == "“" and s[-1] == "”":
        s = s[1:-1]
    return s.strip()


def _strip_sub_headings(text: str) -> str:
    """Drop `### ...` sub-sections and fenced blocks from a narrated body so
    what remains reads as the AI's response summary."""
    without_fences = re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.DOTALL)
    lines = [ln for ln in without_fences.splitlines()
             if not ln.startswith("#") and ln.strip() not in ("", "---")]
    return "\n".join(lines).strip()


def _fields(block: str) -> tuple[dict[str, str], list[tuple[int, int]]]:
    """블록의 라벨 필드를 족별로 모은다. 두 번째 값은 사용자 입력이 차지한 줄 범위다.

    값은 라벨 줄의 나머지와, 다음 라벨·헤딩·구분선 전까지의 줄이다. 같은 족이
    여러 번 나오면 처음 것을 쓴다. AI 쪽은 우선순위가 가장 높은 족을 쓴다.
    """
    lines = block.splitlines()
    # 펜스 안의 줄은 라벨도 끝도 아니다 — 원문을 옮겨 적은 것이다.
    fenced = []
    in_fence = False
    for ln in lines:
        opens = ln.lstrip().startswith("```")
        fenced.append(in_fence or opens)
        if opens:
            in_fence = not in_fence
    found: dict[str, str] = {}
    input_spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        hit = None if fenced[i] else _label_at(lines[i])
        if hit is None:
            i += 1
            continue
        family, first = hit
        j = i + 1
        while j < len(lines) and (fenced[j] or (
                _label_at(lines[j]) is None and not _STOP.match(lines[j]))):
            j += 1
        value = _clean("\n".join([first, *lines[i + 1:j]]))
        if family == "user_input":
            input_spans.append((i, j))
        if family != "other" and value:
            found.setdefault(family, value)
        i = j
    fields = {k: v for k, v in found.items() if not k.startswith("ai_response:")}
    ai = sorted(k for k in found if k.startswith("ai_response:"))
    if ai:
        fields["ai_response"] = found[ai[0]]
    return fields, input_spans


def _sections(markdown: str) -> list[tuple[str, str]]:
    """`##` 헤딩 단위의 (헤딩, 본문) 목록.

    **타임스탬프 헤딩을 쓰는 로그에서는 타임스탬프 없는 `##`가 앞 항목의 일부다.**
    에이전트가 항목 안의 질문별 답을 `### Question N` 대신 `## Question N`으로 적은
    로그가 실재한다(실측: 항목 하나가 `## Question 1`~`8`로 쪼개져 답이 그 항목에서
    사라졌다). 항목 헤딩의 과반이 타임스탬프를 달고 있으면 그것이 이 파일의 항목
    표시이고, 달지 않은 `##`는 하위 절로 읽어 앞 항목에 붙인다.

    코드 펜스 안의 `##`는 헤딩이 아니다 — 사용자가 답한 질문 파일을 원문 그대로
    펜스에 옮겨 적은 항목이 있다.
    """
    raw: list[tuple[str, str]] = []
    body: list[str] = []
    heading: str | None = None
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _ENTRY.match(line)
        if m:
            if heading is not None:
                raw.append((heading, "\n".join(body)))
            heading, body = m.group(1).strip(), []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        raw.append((heading, "\n".join(body)))
    stamped = sum(1 for h, _ in raw if _HEADING_STAMP.match(h))
    if not raw or stamped * 2 < len(raw):
        return raw
    merged: list[tuple[str, str]] = []
    for heading, body in raw:
        if merged and not _HEADING_STAMP.match(heading):
            prev_h, prev_b = merged[-1]
            merged[-1] = (prev_h, f"{prev_b}\n### {heading}\n{body}")
        else:
            merged.append((heading, body))
    return merged


def parse_audit_file(markdown: str) -> list[AuditEntry]:
    sections = _sections(markdown)
    entries: list[AuditEntry] = []
    position = 0
    for heading, body in sections:
        block = _split_inline_markers(body)
        stamp = _HEADING_STAMP.match(heading)
        title = stamp.group("title").strip() if stamp else heading

        fields, input_spans = _fields(block)
        sub_input = _SUBHEAD_INPUT.search(block)
        if not fields and sub_input is None and stamp is None:
            # A heading with no fields and no timestamp is prose (a findings
            # section, the doc title's sibling sections, etc.), not a logged
            # interaction. Counting it would put blank rows in the review panels.
            continue

        if sub_input is not None:
            raw = sub_input.group("fenced") or sub_input.group("plain") or ""
            fields.setdefault("user_input", raw.strip())
            if not fields.get("ai_response"):
                fields["ai_response"] = _strip_sub_headings(block[sub_input.end():])
        if not fields.get("ai_response"):
            # 라벨이 붙은 AI 쪽 필드가 없으면 섹션의 서술이 곧 AI의 기록이다 —
            # 사용자 입력 줄은 빼고 읽는다.
            lines = block.splitlines()
            keep = [ln for k, ln in enumerate(lines)
                    if not any(a <= k < b for a, b in input_spans)]
            fields["ai_response"] = _strip_sub_headings("\n".join(keep))
        if not fields.get("timestamp") and stamp is not None:
            fields["timestamp"] = stamp.group("ts")
        # Heading text is the best available context for these entries.
        if not fields.get("context"):
            fields["context"] = title

        # Legacy "## Entry N:" logs carry their own number; semantic headings
        # get a 1-based sequence so the UI can sort newest-first.
        legacy = _LEGACY_ENTRY_NAME.match(heading)
        position += 1
        index = int(legacy.group(1)) if legacy else position

        entries.append(AuditEntry(
            index=index,
            timestamp=fields.get("timestamp", ""),
            user_input=redact_credentials(fields.get("user_input", "")),
            ai_response=redact_credentials(fields.get("ai_response", "")),
            context=redact_credentials(fields.get("context", "")) or None,
        ))
    if not entries and len(sections) > 1:
        # 헤딩이 여럿인데 하나도 못 읽었다면 파서가 모르는 모양이다. 빈 패널은 "기록이
        # 없다"와 구별되지 않으므로 여기서 드러낸다.
        _log.warning("audit.md has %d sections but none parsed as an entry",
                     len(sections))
    return entries
