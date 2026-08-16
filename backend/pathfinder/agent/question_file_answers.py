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

    best: tuple[int, float] | None = None
    best_write: tuple[Path, str] | None = None
    # rglob은 순서를 보장하지 않는다 — 동점 처리를 재현 가능하게 하려면 정렬이
    # 필요하다(mtime까지 같은 경우 경로 순서로 결정된다).
    for path in sorted(docs.rglob(_GLOB)):
        found = _match_file(path, wanted)
        if found is None:
            continue
        new_md, hits = found
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        score = (hits, mtime)
        if best is None or score > best:
            best, best_write = score, (path, new_md)

    if best_write is None:
        return []
    path, new_md = best_write
    try:
        path.write_text(new_md, encoding="utf-8")
    except OSError:
        _log.exception("question file not writable: %s", path)
        return []
    return [path.relative_to(Path(workspace)).as_posix()]


def _wanted_by_text(sdk_questions: list[dict],
                    answers: dict[str, str]) -> dict[str, str]:
    """{정규화된 질문 텍스트: 답변 값}.

    answers의 키는 이 라운드 안의 1-based 인덱스다(프론트 QuestionForm이
    `String(q.number)`로 보내고 question_file_from_sdk가 그 번호를 매긴다).
    범위를 벗어난 키를 건너뛰는 것은 claude_driver가 sdk_answers를 조립할 때
    하는 방어와 같다 — 0이나 음수를 그대로 인덱스로 쓰면 파이썬이 뒤에서부터
    세어 **다른 질문에 답이 붙는다.**

    질문 텍스트의 필드명이 드라이버마다 다르다: SDK AskUserQuestion은
    `question`, Strands `ask_questions`의 정규화 페이로드는 `text`다
    (questions_payload._normalize_question). 둘을 여기서 함께 받는 이유는 두
    드라이버가 같은 되기록을 쓰기 때문이다 — 한쪽만 지원하면 그 드라이버에서만
    조용히 빈 칸이 남는다.
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


def _match_file(path: Path, wanted: dict[str, str]) -> tuple[str, int] | None:
    """이 파일에 답을 심은 결과와 매칭 수. 심을 것이 없으면 None."""
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

    mapping = {q.number: wanted[_norm(q.text)]
               for q in qfile.questions if _norm(q.text) in wanted}
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
    return new_md, len(mapping)
