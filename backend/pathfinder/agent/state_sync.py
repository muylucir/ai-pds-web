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


def _stage_base(line_name: str) -> str:
    """체크라인 이름에서 노트 접미사(' — note' 또는 ' - note')를 제거한
    기본 이름을 반환한다. parse_state_file의 _SPLIT과 동일한 관용."""
    return line_name.split(" — ")[0].split(" - ")[0].strip()


def _names_match(line_name: str, stage: str) -> bool:
    """완화된 부분 포함 매칭(예: 'Envision' ↔ 'Envision (Path A)').

    주의: 이 함수 하나만으로는 파서(parse_state_file)의 선택 규칙과
    정확히 대응하지 않는다 — 파서는 정확 일치를 먼저 시도하고, 없을 때만
    부분 포함 중 가장 긴 이름으로 폴백한다. 그 정확-일치-우선 로직은
    upsert_stage의 2단계 탐색(먼저 _stage_base 정확 일치, 없으면 이 함수로
    폴백)이 담당하며, 이 함수 자체는 폴백 단계의 부분 포함 판정만 한다.
    """
    base = _stage_base(line_name)
    return base == stage or stage in base or base in stage


def upsert_stage(markdown: str | None, stage: str, status: str) -> str:
    """상태 파일 전문에 스테이지 전이를 반영해 갱신본을 반환한다.

    - markdown=None(파일 없음): 최소 골격 생성.
    - 기존 체크리스트에서 이름이 맞는 줄의 체크박스를 갱신(노트는 보존).
      매칭은 2단계: (1) 기본 이름 정확 일치를 우선하고, (2) 없을 때만
      부분 포함 매칭 중 기본 이름이 가장 긴 줄로 폴백한다 — parse_state_file의
      정확-일치-우선/최장-부분-일치 폴백 규칙과 동일한 우선순위.
    - 줄이 없으면 ## Stage Progress 블록 끝에 추가.
    - Current Stage는 in_progress/pending일 때만 stage로 갱신(completed는 유지).
    """
    if markdown is None or markdown.strip() == "":
        return _SKELETON.format(stage=stage, mark=_mark(status))

    lines = markdown.splitlines()
    out: list[str] = []
    in_progress_block = False
    block_end = -1          # ## Stage Progress 블록의 마지막 체크라인 인덱스(out 기준)
    candidates: list[tuple[int, str]] = []  # (out 인덱스, 체크라인 본문) — 블록 내 전체 체크라인
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
                candidates.append((len(out), m.group(2)))
                out.append(line)
                continue
            elif line.startswith("## "):
                in_progress_block = False
        out.append(line)

    # 2단계 선택: 먼저 기본 이름 정확 일치, 없으면 부분 포함 중 최장 기본 이름.
    target_idx: int | None = None
    target_body: str | None = None
    exact = [(idx, body) for idx, body in candidates if _stage_base(body) == stage]
    if exact:
        target_idx, target_body = exact[0]
    else:
        partial = [(idx, body) for idx, body in candidates if _names_match(body, stage)]
        if partial:
            target_idx, target_body = max(partial, key=lambda pair: len(_stage_base(pair[1])))

    matched = target_idx is not None
    if matched:
        out[target_idx] = f"- [{_mark(status)}] {target_body}"

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
        text = _CURRENT.sub(lambda m: m.group(1) + stage, text, count=1)
    return text
