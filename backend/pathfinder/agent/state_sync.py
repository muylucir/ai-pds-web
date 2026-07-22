# backend/pathfinder/agent/state_sync.py — report_stage의 aiplc-state.md 보장.
# 방법론 룰은 스테이지 전이마다 상태 파일 갱신을 명시하지만 이는 프롬프트 규약일
# 뿐이라, 에이전트가 건너뛰면 대시보드/목록/게이트 배지가 전부 빈다(실사고:
# qa-test 프로젝트). 이 모듈이 도구 호출 시점에 기계적으로 upsert한다.
# 출력은 반드시 parsers/state.py의 parse_state_file이 파싱 가능한 포맷.
from __future__ import annotations
import re

_CURRENT = re.compile(r"^(- \*\*Current Stage\*\*: ).*$", re.MULTILINE)
_PROGRESS_HEADER = re.compile(r"^## Stage Progress\s*$", re.MULTILINE)
_CHECK_LINE = re.compile(r"^- \[([ xX])\]\s*(.+)$")

_SKELETON = """# AI-PLC State

- **Current Stage**: {stage}

## Stage Progress
- [{mark}] {stage}
"""


def _mark(status: str) -> str:
    return "x" if status == "completed" else " "


def _names_match(line_name: str, stage: str) -> bool:
    """파서(parse_state_file)의 current-stage 매칭 관용과 동일: 정확 일치
    또는 부분 포함('Envision' ↔ 'Envision (Path A)')."""
    base = line_name.split(" — ")[0].split(" - ")[0].strip()
    return base == stage or stage in base or base in stage


def upsert_stage(markdown: str | None, stage: str, status: str) -> str:
    """상태 파일 전문에 스테이지 전이를 반영해 갱신본을 반환한다.

    - markdown=None(파일 없음): 최소 골격 생성.
    - 기존 체크리스트에서 이름이 맞는 줄의 체크박스를 갱신(노트는 보존).
    - 줄이 없으면 ## Stage Progress 블록 끝에 추가.
    - Current Stage는 in_progress/pending일 때만 stage로 갱신(completed는 유지).
    """
    if markdown is None or markdown.strip() == "":
        return _SKELETON.format(stage=stage, mark=_mark(status))

    lines = markdown.splitlines()
    out: list[str] = []
    in_progress_block = False
    block_end = -1          # ## Stage Progress 블록의 마지막 체크라인 인덱스(out 기준)
    matched = False
    for line in lines:
        if _PROGRESS_HEADER.match(line):
            in_progress_block = True
            out.append(line)
            block_end = len(out)
            continue
        if in_progress_block:
            m = _CHECK_LINE.match(line.strip())
            if m:
                block_end = len(out) + 1
                if not matched and _names_match(m.group(2), stage):
                    matched = True
                    body = m.group(2)
                    out.append(f"- [{_mark(status)}] {body}")
                    continue
            elif line.startswith("## "):
                in_progress_block = False
        out.append(line)

    if not matched:
        if block_end == -1:
            # ## Stage Progress 블록 자체가 없음 — 문서 끝에 블록째 추가
            if out and out[-1].strip() != "":
                out.append("")
            out.append("## Stage Progress")
            out.append(f"- [{_mark(status)}] {stage}")
        else:
            out.insert(block_end, f"- [{_mark(status)}] {stage}")

    if not any(_CURRENT.match(line) for line in out):
        # Current Stage 줄이 없으면 첫 헤딩(# ...) 바로 다음에 삽입한다.
        # 헤딩이 없으면 문서 맨 앞에 붙인다.
        insert_at = 0
        for idx, line in enumerate(out):
            if line.startswith("# "):
                insert_at = idx + 1
                break
        out.insert(insert_at, "")
        out.insert(insert_at + 1, f"- **Current Stage**: {stage}")

    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"

    if status != "completed":
        text = _CURRENT.sub(rf"\g<1>{stage}", text, count=1)
    return text
