# backend/aipds/audit_log.py — 사용자 입력을 audit.md에 원문 그대로 남기는 쪽은 웹이다.
#
# **왜 웹인가(2026-10-07 실측).** 상류 룰은 "모든 사용자 입력을 원문 그대로, 타임스탬프와
# 함께" audit.md에 남기라고 한다(core-workflow.md Audit Logging). 그 일을 에이전트가 하던
# 동안 세 가지가 어긋났다.
# - **시각을 지어냈다.** tobacco는 실제로 12:46에 쓴 항목에 "12:50:00"·"13:05:00"을
#   적었다 — 미래이고, 정각 단위의 둥근 값이다. 에이전트에게는 시계가 없다.
# - **요약했다.** novadesk-2의 사용자 입력은 "검증 설문 결과 제공… 5명 응답. Q1:5.0/5…"로
#   남았다. 룰이 금지하는 것이다.
# - **느렸다.** 매 턴 사용자 원문을 모델이 다시 타이핑했다. 감사 Edit 하나가 0.9~3.2K자,
#   5~31초였다(chicken·tobacco).
# 애초에 에이전트는 원문을 보지 못한다 — 폼 답변은 웹이 "질문에 답했습니다: …"로 조립해
# 넘긴다. 원문을 가진 쪽이 쓰는 것이 룰의 요구에 더 가깝다. 에이전트는 자기 응답·결정·
# 맥락만 덧붙인다(discovery-config/CLAUDE.md의 Required artifacts 절).
#
# **원문은 인용 블록(`> `)으로 싣는다.** 사용자는 마크다운을 붙여 넣는다 — 헤딩이나 코드
# 펜스가 줄 머리에 그대로 오면 감사 로그의 항목 경계가 깨진다(parsers/audit.py는 `##`로
# 항목을, ``` 로 펜스를 가른다). 인용 표시가 붙은 줄은 헤딩도 펜스도 라벨도 아니고,
# 파서는 그 표시를 떼어 원문을 돌려준다.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aipds.parsers.redaction import redact_credentials

AUDIT_KEY = "aiplc-docs/audit.md"

#: 웹이 쓰는 항목의 헤딩 머리. 파서가 이것으로 웹의 항목을 알아보고 뒤따르는 에이전트
#: 항목과 짝짓는다(parsers/audit.py). 문서는 프로젝트 언어를 따르므로 두 벌이다.
WEB_INPUT_HEADINGS = {"ko": "사용자 입력 (AI-PDS Web 기록)",
                      "en": "User Input (recorded by AI-PDS Web)"}

#: 파일이 아직 없을 때의 제목. 첫 턴에서 에이전트보다 웹이 먼저 쓴다.
_TITLE = "# AI-PLC Audit Log"

_SOURCES = {
    "ko": {"chat": "채팅", "answers": "질문 답변", "approval": "문서 승인"},
    "en": {"chat": "chat", "answers": "question answers", "approval": "document approval"},
}


@dataclass(frozen=True)
class UserInput:
    """턴을 연 사용자 입력. 라우트가 만든다 — 무엇이 사용자의 말인지는 라우트만 안다
    (질문 답변 턴의 텍스트는 답 뒤에 에이전트용 지시를 붙인다)."""
    text: str
    source: str              # "chat" | "answers" | "approval"
    language: str = "ko"
    detail: str | None = None   # 답변 턴이면 질문 파일 이름


def _lang(language: str) -> str:
    return "en" if language == "en" else "ko"


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote(text: str) -> str:
    """원문을 줄마다 인용 블록으로. 빈 줄도 `>`로 남겨 인용이 끊기지 않게 한다."""
    lines = text.strip("\n").splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def entry(record: UserInput, now: datetime | None = None) -> str:
    """audit.md에 덧붙일 항목 하나(앞뒤 빈 줄 포함)."""
    lang = _lang(record.language)
    source = _SOURCES[lang].get(record.source, record.source)
    if record.detail:
        source = f"{source} · {record.detail}"
    return "\n".join([
        "",
        f"## {WEB_INPUT_HEADINGS[lang]} — {source}",
        f"**Timestamp**: {_stamp(now)}",
        "**User Input**:",
        _quote(redact_credentials(record.text)),
        "",
    ])


def append(local_root: Path, record: UserInput, now: datetime | None = None) -> str:
    """워크스페이스의 audit.md에 항목을 붙이고 그 키를 돌려준다. 없으면 만든다."""
    path = Path(local_root) / AUDIT_KEY
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else f"{_TITLE}\n"
    path.write_text(existing.rstrip("\n") + "\n" + entry(record, now), encoding="utf-8")
    return AUDIT_KEY
