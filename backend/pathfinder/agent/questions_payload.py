# backend/pathfinder/agent/questions_payload.py — ask_questions 페이로드 정규화.
#
# 왜 필요한가: 마크다운 경로(parsers/questions.py)는 is_other를 코드가 판정한다
# (letter == "X" 또는 텍스트가 "other"로 시작). 반면 ask_questions는 모델이 만든
# dict를 그대로 UI로 흘려보내므로, 프롬프트 규약(QUESTIONS_SCHEMA_HINT)을 어긴
# 페이로드가 그대로 렌더된다. 실측 사고: is_other가 두 개(B와 X) 와서 두 옵션이
# 모두 "Other — 직접 입력"으로 렌더됐고, 둘이 같은 otherActive 상태를 공유해
# 선택이 서로를 덮어썼다. 프롬프트를 조여도 근본 해결이 아니다 — 모델은 이미
# 규약을 받고도 틀렸고, 재전송 왕복은 사용자에게 빈 대기로 보인다.
#
# 방침: 고칠 수 있는 것은 코드가 조용히 교정하고(사용자에게 보이는 화면을
# 살리는 게 우선), 질문 자체가 성립하지 않는 경우만 ValueError로 거부해 모델이
# 다시 만들게 한다.
from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger("pathfinder.agent")

# 마크다운 파서와 동일한 순서(_LETTERS는 builder의 A..J와 별개 — 질문 옵션은
# 스키마상 A..F + X).
_LETTERS = "ABCDEFGHIJ"
_OTHER_LETTER = "X"
_OTHER_TEXT = "Other — 직접 입력"


def _looks_like_other(letter: str, text: str) -> bool:
    """마크다운 파서(parsers/questions.py)와 같은 규칙을 쓴다 — 두 경로가
    갈리면 같은 질문이 입력 형식에 따라 다르게 렌더된다."""
    return letter == _OTHER_LETTER or text.strip().lower().startswith("other")


def _normalize_options(raw_options: list[Any]) -> list[dict]:
    """letter 보정 → Other 판정 → Other 1개로 축약.

    Other를 마지막 하나만 남기는 이유: 관례상 Other는 목록 끝이고, 앞쪽에
    is_other=True로 온 것은 모델이 실질 선택지를 잘못 표시한 경우다(실측 사고의
    B가 그랬다). 강등된 옵션은 텍스트를 살려 되돌린다.
    """
    opts: list[dict] = []
    used: set[str] = set()
    for i, raw in enumerate(raw_options):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        letter = str(raw.get("letter") or "").strip()
        # letter 누락/중복 보정: 빈 배지가 뜨거나 프론트의 key/라디오 value가
        # 충돌해 선택이 서로를 덮어쓴다.
        if not letter or letter in used:
            letter = next((c for c in _LETTERS if c not in used), None) or f"Z{i}"
        used.add(letter)
        opts.append({
            "letter": letter,
            "text": text,
            # 모델의 is_other는 참고만 하고, 판정은 마크다운 경로와 같은 규칙으로
            # 다시 한다 — X를 False로 보내 자유 입력창이 사라진 경우도 여기서 잡힌다.
            "is_other": bool(raw.get("is_other")) or _looks_like_other(letter, text),
            "recommended": bool(raw.get("recommended")),
        })

    others = [i for i, o in enumerate(opts) if o["is_other"]]
    for i in others[:-1]:
        opts[i]["is_other"] = False
        # is_other로 온 옵션은 텍스트가 비어 있을 수 있다(UI가 문구를 넣어주므로
        # 모델이 생략). 강등하면 고를 수 없는 빈 보기가 되므로 라벨을 채운다.
        if not opts[i]["text"] or _looks_like_other(opts[i]["letter"], opts[i]["text"]):
            opts[i]["text"] = f"보기 {opts[i]['letter']}"
    if others:
        opts[others[-1]]["text"] = opts[others[-1]]["text"] or _OTHER_TEXT
    return opts


def _normalize_question(raw: Any, number: int) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"질문 {number}이 객체가 아니다: {type(raw).__name__}")
    options = _normalize_options(raw.get("options") or [])
    # Other 하나만 있는 질문은 객관식이 아니다 — 자유 입력이면 채팅으로 충분하고,
    # 폼으로 띄우면 사용자는 선택지 없는 빈 카드를 본다.
    if not [o for o in options if not o["is_other"]]:
        raise ValueError(
            f"질문 {number}에 고를 수 있는 보기가 없다 — Other 외에 최소 1개의 "
            "실질 보기가 필요하다")
    text = str(raw.get("text") or "").strip()
    if not text:
        raise ValueError(f"질문 {number}의 text가 비어 있다")
    category = raw.get("category")
    return {
        # number는 라디오 name(q{number})과 답변 dict 키로 쓰인다. 모델이 준 값을
        # 믿지 않고 순번을 다시 매긴다 — 중복되면 두 질문의 라디오가 같은 그룹이
        # 되어 서로를 해제한다.
        "number": number,
        "category": str(category).strip() if category else None,
        "text": text,
        "answer": None,
        "multi_select": bool(raw.get("multi_select")),
        "options": options,
    }


def normalize_questions_payload(payload: Any) -> dict:
    """모델이 만든 questions_file을 프론트 QuestionsPayload 계약으로 맞춘다.

    고칠 수 있는 위반은 조용히 교정하고, 질문이 성립하지 않으면 ValueError를
    던진다(도구가 그 메시지를 모델에게 돌려줘 다시 만들게 한다).
    """
    # 모델이 dict 대신 JSON 문자열을 넘기는 경우가 실제로 있다(실측: "질문 폼
    # 전송 형식에 오류가 있어 다시 보내겠습니다"). 파싱되면 받아준다 — 재전송
    # 왕복은 사용자에게 빈 대기로 보인다.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            raise ValueError(f"questions_file이 유효한 JSON이 아니다: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError(
            f"questions_file은 객체여야 한다 (받은 타입: {type(payload).__name__})")

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("questions_file.questions가 비어 있다 — 최소 1개의 질문이 필요하다")

    questions = [_normalize_question(q, i + 1) for i, q in enumerate(raw_questions)]

    preamble = payload.get("preamble")
    return {
        "name": str(payload.get("name") or "questions").strip() or "questions",
        "preamble": str(preamble).strip() if preamble else None,
        # 프론트 계약: parse_ok=True + raw_markdown=None이어야 RawMarkdownFallback이
        # 아니라 폼으로 렌더된다.
        "parse_ok": True,
        "raw_markdown": None,
        "questions": questions,
    }
