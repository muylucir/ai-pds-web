# backend/aipds/agent/questions_payload.py — ask_questions 페이로드 정규화.
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

_log = logging.getLogger("aipds.agent")

# 마크다운 파서와 동일한 순서(_LETTERS는 builder의 A..J와 별개 — 질문 옵션은
# 스키마상 A..F + X).
_LETTERS = "ABCDEFGHIJ"
_OTHER_LETTER = "X"
_OTHER_TEXT = "Other — 직접 입력"


def _looks_like_other(letter: str, text: str) -> bool:
    """마크다운 파서(parsers/questions.py)와 같은 규칙을 쓴다 — 두 경로가
    갈리면 같은 질문이 입력 형식에 따라 다르게 렌더된다."""
    return letter == _OTHER_LETTER or text.strip().lower().startswith("other")


def _normalize_options(raw_options: list[Any], *, guess_other: bool = True) -> list[dict]:
    """letter 보정 → Other 판정 → Other 1개로 축약.

    Other를 마지막 하나만 남기는 이유: 관례상 Other는 목록 끝이고, 앞쪽에
    is_other=True로 온 것은 모델이 실질 선택지를 잘못 표시한 경우다(실측 사고의
    B가 그랬다). 강등된 옵션은 텍스트를 살려 되돌린다.

    guess_other=False는 SDK AskUserQuestion 경로(question_file_from_sdk)용이다
    — 그 경로는 모델이 이미 구조화된 options를 주므로 프로즈에서 Other를
    추측할 이유가 없다("Other database"처럼 실제 옵션 라벨이 "other"로
    시작하면 _looks_like_other가 오판해 진짜 옵션의 텍스트를 지워버린다).
    마크다운/Discovery 경로(normalize_questions_payload의 기본값)는 계속
    휴리스틱을 쓴다 — 모델이 dict를 직접 만들고 실측 사고가 그 경로에서
    있었기 때문이다.
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
        # 모델의 is_other는 참고만 하고, 판정은 마크다운 경로와 같은 규칙으로
        # 다시 한다 — X를 False로 보내 자유 입력창이 사라진 경우도 여기서
        # 잡힌다. guess_other=False면 텍스트/letter 추측을 끄고 모델이 준
        # is_other만 믿는다.
        is_other = bool(raw.get("is_other")) or (
            guess_other and _looks_like_other(letter, text))
        opts.append({
            "letter": letter,
            "text": text,
            "is_other": is_other,
            "recommended": bool(raw.get("recommended")),
        })

    others = [i for i, o in enumerate(opts) if o["is_other"]]
    for i in others[:-1]:
        opts[i]["is_other"] = False
        # is_other로 온 옵션은 텍스트가 비어 있을 수 있다(UI가 문구를 넣어주므로
        # 모델이 생략). 강등하면 고를 수 없는 빈 보기가 되므로 라벨을 채운다.
        if not opts[i]["text"] or (
                guess_other and _looks_like_other(opts[i]["letter"], opts[i]["text"])):
            opts[i]["text"] = f"보기 {opts[i]['letter']}"
    if others:
        opts[others[-1]]["text"] = opts[others[-1]]["text"] or _OTHER_TEXT
    return opts


def _normalize_question(raw: Any, number: int, *, guess_other: bool = True) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"질문 {number}이 객체가 아니다: {type(raw).__name__}")
    options = _normalize_options(raw.get("options") or [], guess_other=guess_other)
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


def normalize_questions_payload(payload: Any, *, guess_other: bool = True) -> dict:
    """모델이 만든 questions_file을 프론트 QuestionsPayload 계약으로 맞춘다.

    고칠 수 있는 위반은 조용히 교정하고, 질문이 성립하지 않으면 ValueError를
    던진다(도구가 그 메시지를 모델에게 돌려줘 다시 만들게 한다).

    guess_other: 텍스트가 "other"로 시작하면 Other로 간주하는 휴리스틱을 켤지
    여부. 기본 True는 마크다운/Discovery 경로(ask_questions)용 — 모델이 만든
    dict를 그대로 신뢰할 수 없어 생긴 방침이다. question_file_from_sdk는
    False로 호출한다: SDK가 이미 명시적 options를 주므로 프로즈 추측이
    필요 없고, 오히려 "Other database"처럼 진짜 옵션의 라벨을 오판해
    지워버린다. is_other 중복 축약(마지막 하나만 Other로 남기는 로직)은
    두 경로 모두에서 그대로 적용된다 — 이것을 끄는 것은 아니다."""
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

    questions = [_normalize_question(q, i + 1, guess_other=guess_other)
                 for i, q in enumerate(raw_questions)]

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


# SDK AskUserQuestion의 input을 프론트 QuestionFile 형태로 옮긴다. letter는 SDK
# 옵션 순서를 그대로 인덱싱한다 — 답변을 SDK 라벨로 되번역할 때(_answer_to_sdk)
# 그 인덱스가 키이므로 순서가 어긋나면 다른 보기를 고른 것이 된다.
#
# builder._to_question_file에서 옮겨온 것이다. 두 경로가 한 함수로 수렴하면
# is_other 중복 교정(normalize_questions_payload)이 프로토타입 빌드에도 적용된다.
def normalize_sdk_questions(raw: object) -> list[dict]:
    """AskUserQuestion의 `questions` 인자를 list[dict]로 정규화한다.

    모델이 이 인자를 **직렬화된 JSON 문자열**로 넘기는 일이 있다(실측: 한
    세션의 18라운드 중 3건). 여기서 막지 않으면 question_file_from_sdk가
    문자열을 문자 단위로 훑다가 AttributeError로 터진다 — 그 예외는 permission
    콜백 밖으로 새어 턴을 죽인다.

    ⚠️ **관측된 그 3건은 이 함수로 살아나지 않는다.** CLI가 우리 콜백을 부르기
    **전에** 스키마 검증으로 거절했다(실측 근거: tool_use와 `InputValidationError`
    tool_result가 같은 트랜스크립트 파일에 즉시 짝지어 있고, 백엔드 로그에는
    그 시간대에 관련 경고가 한 줄도 없다). 모델은 그 에러를 읽고 다음 턴에
    올바른 배열로 재시도하므로 사용자에게 질문이 유실되지는 않는다. 이 함수는
    같은 shape가 콜백까지 도달하는 경로(SDK/CLI 버전 차이)에 대한 방어다.

    리스트가 아니거나 파싱이 실패하면 빈 리스트 — 호출부는 옵션 없는 페이로드와
    같은 경로(ValueError → 모델에게 거부 사유 반환)를 탄다.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [q for q in raw if isinstance(q, dict)]


def question_file_from_sdk(sdk_questions: list[dict], *, name: str) -> dict:
    questions = []
    for i, q in enumerate(sdk_questions, start=1):
        raw_options = q.get("options") or []
        options = []
        for j, o in enumerate(raw_options):
            label = str(o.get("label") or "")
            desc = str(o.get("description") or "")
            text = f"{label} — {desc}".rstrip(" —") if desc else label
            options.append({
                "letter": _LETTERS[j] if j < len(_LETTERS) else f"Z{j}",
                "text": text, "is_other": False, "recommended": False,
            })
        # 자유 입력 선택지를 붙인다. AskUserQuestion에는 is_other에 해당하는
        # 필드가 없고 이 경로는 guess_other=False로 정규화하므로, 이걸 붙이지
        # 않으면 Other가 생길 수 있는 경로가 하나도 없다 — 사용자는 모델이 제시한
        # 선택지 밖의 일을 시킬 방법이 없어진다(실측: "다시 빌드" 후 무엇을
        # 진행할지 묻는 질문에 원하는 항목이 없어 아무것도 지시할 수 없었다).
        #
        # letter는 X다. A/B/C 흐름에 끼우면 안 되는 이유가 계약에 있다 —
        # builder._answer_to_sdk가 `sdk_options[_LETTERS.find(letter)]`로 답변을
        # SDK 라벨로 되번역하므로, 실제 옵션의 letter가 한 칸이라도 밀리면 모든
        # 답변이 엉뚱한 옵션으로 번역된다. X는 _LETTERS(A-J) 밖이라 그 인덱스
        # 계산에 관여하지 않고, 매칭되지 않는 값은 자유 텍스트로 그대로
        # 통과한다(builder.py의 `return value  # free text (Other)`).
        #
        # 모델이 "Other" 라벨을 이미 넣었더라도 그대로 하나 더 붙인다. 붙이지
        # 않는 쪽을 먼저 시도했는데(_looks_like_other로 감지) 더 나빴다: 이 경로는
        # guess_other=False로 정규화하므로 모델이 넣은 그 옵션의 is_other는 계속
        # False로 남고, 결과는 **자유 입력창이 하나도 없는** 상태였다(실측). 즉
        # 감지해서 건너뛰면 고치려던 문제가 그대로 재현된다.
        #
        # 중복으로 보이는 옵션이 하나 생기는 것은 감수한다 — 모델의 "Other"는
        # 평범한 라디오 항목으로, X는 자유 입력창으로 렌더되므로 사용자가 할 수
        # 있는 일은 줄지 않는다. 정규화의 is_other 축약(앞의 것을 강등)도 X가
        # 유일한 is_other라 발동하지 않는다.
        if options:
            options.append({"letter": _OTHER_LETTER, "text": _OTHER_TEXT,
                            "is_other": True, "recommended": False})
        questions.append({
            "number": i,
            "category": q.get("header") or None,
            "text": str(q.get("question") or ""),
            "answer": None,
            "multi_select": bool(q.get("multiSelect")),
            "options": options,
        })
    # 정규화가 최종 계약을 강제한다 — 옵션 없는 질문은 여기서 ValueError.
    # guess_other=False: SDK 옵션은 이미 구조화되어 있으니 텍스트로 Other를
    # 추측하지 않는다("Other database" 같은 실제 옵션 라벨이 오판되는 것을
    # 막는다). is_other 중복 축약은 그대로 적용된다.
    return normalize_questions_payload(
        {"name": name, "preamble": None, "questions": questions},
        guess_other=False)
