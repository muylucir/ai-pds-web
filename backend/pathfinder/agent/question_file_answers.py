# backend/pathfinder/agent/question_file_answers.py — 제출된 답변을 질문 파일의
# `[Answer]:` 칸에 되기록한다.
#
# **왜 필요한가.** ai-plc 워크플로우는 질문 파일이 답안지라는 전제로 돌아간다:
# aws-aiplc-rule-details/common/question-format-guide.md가 "Read the question
# file / Extract answers after [Answer]: tags"를 지시하고,
# common/session-continuity.md:31-33은 스테이지를 재개할 때 `strategy-questions.md`
# 같은 파일을 **읽으라고** 한다. Pathfinder는 질문을 AskUserQuestion으로
# 전달하면서 이 칸을 비워 뒀고, 그래서 재개한 세션은 사용자가 이미 내린 결정을
# 파일에서 되찾을 수 없었다.
#
# **왜 번호가 아니라 질문 텍스트로 맞추는가.** AskUserQuestion은 질문 4개 ×
# 보기 4개가 스키마 하드 리밋이다. 10문항 파일은 4+4+2 세 라운드로 쪼개지고 각
# 라운드의 문항 번호는 1부터 다시 시작하므로, 번호로 맞추면 라운드 2의 답이
# 문항 1~4를 덮고 5~8은 영구히 빈 칸으로 남는다 — 에러 없이 **틀린 답이
# 기록된다.** 모델은 파일과 도구에 같은 질문 문장을 쓰므로 그 문장이 라운드
# 경계를 넘는 유일한 안정적 키다.
#
# **왜 한 파일만 고치는가.** 워크스페이스에는 스테이지마다 질문 파일이 쌓이고
# "Would you like to proceed with these settings?" 같은 문장은 여러 파일에
# 중복될 수 있다. 매칭되는 파일 전부에 쓰면 지난 스테이지의 기록이 훼손된다.
# 한 AskUserQuestion 라운드는 한 스테이지에 속하므로 파일도 하나다 — 매칭 수가
# 가장 많은 파일을 고르고, 같으면 최근에 수정된 파일이 이긴다(에이전트가 지금
# 스테이지의 파일을 방금 썼다).
#
# **매칭 실패는 빈 칸으로 남긴다.** 엉뚱한 문항에 답이 박히면 그 파일을 읽는
# 다음 스테이지가 사용자가 하지 않은 결정을 사실로 취급한다. 빈 칸은 사람이
# 알아볼 수 있지만 틀린 답은 알아볼 수 없다.
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from pathlib import Path

from pathfinder.parsers.questions import parse_question_file, serialize_answers

_log = logging.getLogger("pathfinder.agent")

#: 질문 파일 규약(question-format-guide.md의 파일 명명 규칙). Workspace.
#: list_question_files의 glob과 같은 집합을 본다.
_GLOB = "*-questions.md"
_DOCS_DIR = "aiplc-docs"


def _norm(text: object) -> str:
    """매칭 키. 공백 런을 하나로 접고 대소문자를 무시한다.

    파서가 본문 여러 줄을 " "로 join하므로(parsers/questions.py:72) 도구 쪽에
    줄바꿈이 남아 있으면 원문 비교가 어긋난다. 그 이상은 하지 않는다 — 어미나
    문장부호까지 뭉개면 서로 다른 질문이 같은 키로 충돌할 수 있고, 이 함수의
    실패 방향은 "못 찾음"이어야 한다.
    """
    return " ".join(str(text or "").split()).casefold()


def record_answers(workspace: str, sdk_questions: list[dict],
                   answers: dict[str, str]) -> list[str]:
    """답변을 질문 파일에 기록하고, 실제로 바뀐 파일의 상대 경로 목록을 돌려준다.

    **어떤 실패도 예외로 새지 않는다.** 답변은 이미 사용자가 제출했고 턴은
    재개되어야 한다 — 부수 기록의 실패로 턴을 죽이면 그 답변이 사라진다
    (claude_driver._save_answers_quietly와 같은 규율).
    """
    try:
        return _record(workspace, sdk_questions, answers)
    except Exception:
        _log.exception("question-file answer write-back failed")
        return []


def _record(workspace: str, sdk_questions: list[dict],
            answers: dict[str, str]) -> list[str]:
    docs = Path(workspace) / _DOCS_DIR
    if not docs.is_dir():
        # 첫 턴에는 아직 아무 산출물도 없다. 정상 상태다.
        return []

    wanted = _wanted_by_text(sdk_questions, answers)
    if not wanted:
        return []

    miss = _Miss()
    best: _FileMatch | None = None
    # rglob은 순서를 보장하지 않는다 — 동점 처리를 재현 가능하게 하려면 정렬이
    # 필요하다(mtime까지 같은 경우 경로 순서로 결정된다).
    for path in sorted(docs.rglob(_GLOB)):
        found = _match_file(path, wanted, miss)
        if found is None:
            continue
        if best is None or found.score() > best.score():
            best = found

    if best is None:
        # **조용히 실패하지 않는다.** 2026-08-16에 되기록이 안 됐는데 로그가 텅
        # 비어 있어서 원인을 찾는 데 오래 걸렸다. 최선 후보와 그 점수를 남기면
        # "임계값이 빡빡한가"(0.85에 가까움)와 "엉뚱한 파일을 보고 있는가"(낮음)를
        # 바로 가를 수 있다.
        _log.warning(
            "question-file write-back: no match for %d answer(s); best %.3f "
            "asked=%r candidate=%r", len(wanted), miss.ratio, miss.asked[:60],
            miss.candidate[:60])
        return []
    try:
        best.path.write_text(best.new_md, encoding="utf-8")
    except OSError:
        _log.exception("question file not writable: %s", best.path)
        return []
    rel = best.path.relative_to(Path(workspace)).as_posix()
    if best.fuzzy:
        # 유사 매칭이 일어났다 = **이 라운드의 한글이 깨졌다**(claude-code#83033).
        # 리터럴 UTF-8 지시가 억제에 듣고 있는지를 알 수 있는 유일한 신호이므로
        # info로 남긴다 — 세지 않으면 1층의 효과를 측정할 방법이 없다.
        _log.info("question-file write-back: %s (%d exact, %d fuzzy — corrupted "
                  "Hangul in this round, see claude-code#83033)",
                  rel, best.exact, best.fuzzy)
    else:
        _log.info("question-file write-back: %s (%d exact)", rel, best.exact)
    return [rel]


#: 유사 매칭의 하한과 2등과의 최소 격차.
#:
#: **왜 유사 매칭이 필요한가(claude-code#83033).** 모델이 툴 파라미터의 한글을
#: `\uXXXX` 이스케이프로 쓰면서 hex를 오타내면 그 코드포인트가 "유효하지만 틀린"
#: 음절로 디코드된다. 밀도는 음절의 3~5%이고 호출마다 간헐적으로 켜지므로, 같은
#: 턴에 파일(Write)은 깨끗하고 질문(AskUserQuestion)만 깨질 수 있다. 상류는 공식
#: 미해결이고(모델 팀 이관, CLI로는 복원 불가) hex 오타가 무작위라 역변환도 없다.
#:
#: **왜 이 숫자인가.** keumkang-v3 6라운드 21문항 실측:
#:   맞는 쌍 0.9677 / 가장 비슷한 오답 0.5806 / 그 외 ≤0.375 /
#:   한 라운드 내 최대 0.32~0.40.
#: 0.97과 0.58 사이가 비어 있다. 60자에 3음절이 깨져도 약 0.95, 20자에 1음절이
#: 깨져도 0.95이므로 0.85는 양쪽으로 여유가 크다.
#:
#: **왜 격차도 요구하는가.** 임계값만 보면 비슷한 두 문항이 둘 다 넘을 수 있고,
#: 그때 고르는 것은 동전 던지기다. 격차가 없으면 쓰지 않는다 — 빈 칸은 사람이
#: 알아보지만 틀린 답은 알아볼 수 없다.
_FUZZY_MIN = 0.85
_FUZZY_MARGIN = 0.10


class _FileMatch:
    """한 파일의 매칭 결과. exact/fuzzy를 나눠 들고 있는 이유는 파일 선택에
    쓰이기 때문이다 — 정확 일치가 있는 파일이 유사 일치만 있는 파일을 이긴다."""

    __slots__ = ("path", "new_md", "exact", "fuzzy")

    def __init__(self, path: Path, new_md: str, exact: int, fuzzy: int) -> None:
        self.path, self.new_md, self.exact, self.fuzzy = path, new_md, exact, fuzzy

    @property
    def total(self) -> int:
        return self.exact + self.fuzzy

    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def score(self) -> tuple[int, int, float]:
        """파일 선택 순서. 정확 일치 수 → 전체 매칭 수 → 최근 수정 시각.

        정확 일치를 최우선에 두는 것이 load-bearing이다: 지난 스테이지 파일에
        비슷한 문장이 있으면 유사 매칭이 걸리고, mtime만 보면 그쪽이 더 최근일
        때 정확히 일치하는 파일을 제치고 이긴다.
        """
        return (self.exact, self.total, self.mtime())


#: 매칭에 실패한 라운드의 최선 후보. 진단 전용이다 — 2026-08-16에 되기록이
#: 조용히 실패했고 로그가 텅 비어서 원인 추적이 늦어졌다. 이 숫자가 "임계값이
#: 빡빡한가"와 "엉뚱한 파일을 보고 있는가"를 가른다.
class _Miss:
    __slots__ = ("ratio", "asked", "candidate")

    def __init__(self) -> None:
        self.ratio, self.asked, self.candidate = 0.0, "", ""

    def offer(self, ratio: float, asked: str, candidate: str) -> None:
        if ratio > self.ratio:
            self.ratio, self.asked, self.candidate = ratio, asked, candidate


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _assign(questions, wanted: dict[str, str],
            miss: _Miss) -> tuple[dict[int, str], int, int]:
    """{문항번호: 값}, 정확 일치 수, 유사 일치 수.

    정확 일치를 **먼저** 전부 확정한다. 유사 매칭이 먼저 돌면 깨진 질문이 다른
    문항의 자리를 빼앗고, 정작 그 문항은 빈 칸으로 남는다.
    """
    norms = [(q.number, _norm(q.text)) for q in questions]
    mapping: dict[int, str] = {}
    claimed: set[int] = set()

    remaining = dict(wanted)
    for number, norm in norms:
        if number in claimed:
            continue
        value = remaining.pop(norm, None)
        if value is not None:
            mapping[number], _ = value, claimed.add(number)
    exact = len(mapping)
    if not remaining:
        return mapping, exact, 0

    # 후보를 모아 점수 내림차순으로 배정한다. 한 문항을 두 질문이 노릴 수 있으므로
    # 즉시 배정하지 않고 확신이 큰 쪽부터 자리를 준다.
    candidates: list[tuple[float, str, int]] = []
    for asked, value in remaining.items():
        scored = sorted(((_ratio(asked, norm), number)
                         for number, norm in norms if number not in claimed),
                        reverse=True)
        if not scored:
            continue
        best_ratio, best_number = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else None
        miss.offer(best_ratio, asked,
                   next(n for num, n in norms if num == best_number))
        if best_ratio < _FUZZY_MIN:
            continue
        if runner_up is not None and best_ratio - runner_up < _FUZZY_MARGIN:
            # 어느 쪽인지 정할 근거가 없다 — 쓰지 않는다.
            continue
        candidates.append((best_ratio, asked, best_number))

    fuzzy = 0
    for best_ratio, asked, number in sorted(candidates, reverse=True):
        if number in claimed:
            continue
        mapping[number] = remaining[asked]
        claimed.add(number)
        fuzzy += 1
    return mapping, exact, fuzzy


def _wanted_by_text(sdk_questions: list[dict],
                    answers: dict[str, str]) -> dict[str, str]:
    """{정규화된 질문 텍스트: 답변 값}.

    answers의 키는 이 라운드 안의 1-based 인덱스다(프론트 QuestionForm이
    `String(q.number)`로 보내고 question_file_from_sdk가 그 번호를 매긴다).
    범위를 벗어난 키를 건너뛰는 것은 claude_driver가 sdk_answers를 조립할 때
    하는 방어와 같다 — 0이나 음수를 그대로 인덱스로 쓰면 파이썬이 뒤에서부터
    세어 **다른 질문에 답이 붙는다.**

    질문 텍스트의 필드명이 두 가지다: 원본 AskUserQuestion input은 `question`,
    그것을 UI 계약으로 정규화한 페이로드는 `text`다
    (questions_payload._normalize_question — 프론트가 읽고 답변 레코드에
    저장되는 모양). 드라이버는 원본을 넘기지만 정규화된 모양이 들어와도
    조용히 빈 칸이 되지 않게 둘을 함께 받는다.
    """
    out: dict[str, str] = {}
    for key, value in (answers or {}).items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if index < 1 or index > len(sdk_questions):
            continue
        raw = sdk_questions[index - 1]
        if not isinstance(raw, dict):
            continue
        text = _norm(raw.get("question") or raw.get("text"))
        if text and isinstance(value, str):
            out[text] = value
    return out


def _match_file(path: Path, wanted: dict[str, str],
                miss: _Miss) -> _FileMatch | None:
    """이 파일에 답을 심은 결과. 심을 것이 없으면 None(최선 후보는 _miss에 남는다)."""
    try:
        md = path.read_text(encoding="utf-8")
    except OSError:
        _log.warning("unreadable question file skipped: %s", path)
        return None
    qfile = parse_question_file(path.name, md)
    if not qfile.parse_ok:
        # parse_question_file은 실패를 삼키고 parse_ok=False를 준다. 그 파일에
        # 문항 번호를 부여할 방법이 없으므로 건드리지 않는다.
        return None

    mapping, exact, fuzzy = _assign(qfile.questions, wanted, miss)
    if not mapping:
        return None
    try:
        new_md = serialize_answers(md, mapping)
    except (KeyError, ValueError):
        # serialize_answers는 파일에 없는 번호에 KeyError를 낸다. 위에서 파일의
        # 문항으로만 mapping을 만들었으므로 정상 경로로는 올 수 없다.
        _log.exception("answer serialization refused for %s", path)
        return None
    if new_md == md:
        # 문항은 찾았는데 파일이 그대로다 = `[Answer]:` 줄이 없는 것이다
        # (serialize_answers는 그 줄만 교체한다). 조용히 "기록했다"고 보고하지
        # 않기 위해 남긴다 — 규약을 벗어난 파일을 만든 것은 에이전트다.
        _log.warning("question file has no [Answer]: slot to fill: %s", path)
        return None
    return _FileMatch(path=path, new_md=new_md, exact=exact, fuzzy=fuzzy)
