# backend/aipds/answer_summary.py — 제출된 답변을 사람이 읽는 채팅 텍스트로.
#
# **왜 백엔드인가(2026-08-21).** 이 판별은 `frontend/lib/answerSummary.ts`에만
# 있었고, 그것이 결함의 뿌리였다: 사용자가 화면에서 본 말풍선은 브라우저 안에서만
# 만들어져 서버로 간 적이 없다. 서버는 자기가 만든 다른 문장을 모델에게 보내고
# 그것이 트랜스크립트에 사용자 발화로 기록됐다(routes/answers.py). 라이브 화면은
# 실제 답변을, 복원 화면은 기계 문구를 보여줬고 — 실측: 프로젝트 하나의 user 발화
# 16개 중 13개가 "질문에 답했습니다. 답변은 …의 [Answer]: 태그에 들어 있으니…"였다.
#
# 표현이 둘이면 갈라진다. 그래서 렌더를 여기 한 벌로 두고, 프론트는 서버가 만든
# 문자열을 그대로 쓴다. 프론트가 만들어 서버로 보내는 방향은 택하지 않는다 —
# 이 텍스트는 모델도 읽으므로 프로젝트 언어를 따라야 하고, 두 언어를 프론트가
# 관리하게 되는 것이 2026-08-04 결함의 모양이다(routes/answers.py의 같은 판단).
#
# 옮기면서 고쳐진 것이 하나 더 있다: 프론트에 있던 동안 빈 제출 문구가 **UI 언어**를
# 따랐다. 채팅 말풍선은 프로젝트 언어여야 한다(agent/prompts.py 헤더,
# lib/approvalMarker.ts가 같은 판단을 기록해 뒀다).
#
# **되돌려야 하는 계약.** QuestionCard가 한 문자열에 네 모양을 담는다
# (components/questions/QuestionCard.tsx):
#
#     "A"                  맨 letter
#     "A: 부연"             letter + 자유 서술 부연
#     "A,C"                복수 선택의 콤마 결합
#     "Broker: 큐를 …"      letter처럼 시작하는 자유 텍스트
#
# 앞의 셋만 펼칠 수 있고 **그것을 가려내는 것이 이 모듈의 일 전부**다 — 자유 텍스트를
# `": "`로 쪼개면 살리려던 답변을 훼손한다.
from __future__ import annotations

from aipds.models import QuestionFile, QuestionOption

#: 빈 제출의 말풍선. 빈 문자열이면 빈 말풍선이 되므로 한 줄을 남긴다.
#: frontend `chat.answersSubmitted`와 같은 문구다 — 그쪽은 이제 이 값을 받는다.
_SUBMITTED = {"ko": "답변 제출", "en": "Answers submitted"}


def _letter_text(options: list[QuestionOption], letter: str) -> str | None:
    """그 letter를 가진 **non-Other** 보기의 텍스트. 없으면 None.

    is_other를 제외하는 것이 의도다: 그 letter는 내부 표기이고 `text`는
    플레이스홀더이지 사용자의 답변이 아니다.
    """
    for o in options:
        if not o.is_other and o.letter == letter:
            return o.text
    return None


def _expand_letter_list(options: list[QuestionOption],
                        value: str) -> str | None:
    """`"A,C"` → `"A. 자동 생성, C. 이력 관리"`. 목록이 아니면 None.

    토큰 하나라도 non-Other 보기의 letter가 아니면 **전체를 자유 텍스트로 본다** —
    쉼표가 들어간 문장을 letter 목록으로 오인하지 않기 위한 전부-또는-전무다.
    """
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < 2:
        return None
    expanded: list[str] = []
    for p in parts:
        text = _letter_text(options, p)
        if text is None:
            return None
        expanded.append(f"{p}. {text}")
    return ", ".join(expanded)


def _render_answer(options: list[QuestionOption], value: str) -> str:
    """읽는 사람이 봐야 하는 형태: 보기를 가리키면 보기 텍스트, 자유 텍스트면 원문."""
    multi = _expand_letter_list(options, value)
    if multi is not None:
        return multi

    whole = _letter_text(options, value)
    if whole is not None:
        return f"{value}. {whole}"

    # "A: 부연" — 머리가 **실재하는 non-Other letter일 때만** 이 갈래로 온다.
    # 그 밖의 모든 것("Broker: …" 포함)은 자유 텍스트이고 손대지 않는다.
    idx = value.find(": ")
    if idx > 0:
        head = value[:idx]
        text = _letter_text(options, head)
        if text is not None:
            return f"{head}. {text} — {value[idx + 2:]}"

    return value


def answer_summary(qfile: QuestionFile, answers: dict[int, str],
                   language: str) -> str:
    """제출된 답변 묶음의 채팅 텍스트.

    **문항 목록 순서**를 따른다 — `answers`의 키 순서는 프론트가 보낸 JSON 순서이고,
    사용자가 문항을 건너뛰며 답하면 그 순서가 화면과 어긋난다.

    문항에 없는 키는 버리지 않고 뒤에 붙인다: 낡은 폼이나 번호 재부여가 있으면
    읽는 사람이 질문 한 줄을 잃는 것이 답변 자체를 잃는 것보다 낫다.
    """
    blocks: list[str] = []
    seen: set[int] = set()

    for q in qfile.questions:
        seen.add(q.number)
        value = answers.get(q.number)
        if value is None or not value.strip():
            continue
        blocks.append(f"Q{q.number}. {q.text}\n→ {_render_answer(q.options, value)}")

    for number, value in answers.items():
        if number in seen or not value.strip():
            continue
        blocks.append(f"Q{number}.\n→ {value}")

    if blocks:
        return "\n\n".join(blocks)
    return _SUBMITTED.get(language, _SUBMITTED["ko"])
