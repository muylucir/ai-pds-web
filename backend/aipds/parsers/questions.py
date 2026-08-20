# backend/aipds/parsers/questions.py
from __future__ import annotations
import logging
import re
from aipds.models import Question, QuestionOption, QuestionFile

logger = logging.getLogger(__name__)

#: 문항 헤더. 번호 뒤에 **접미사를 허용한다** — 상류 형식은 `## Question [Number]`
#: 만 규정하고 뒤에 오는 것을 금지하지 않으며, 에이전트는 후속 질문에 설명을 붙인다
#: (실측: `## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)`).
#:
#: 예전에는 `(\d+)\s*$`로 줄 끝을 요구해서 그런 문항을 **아예 못 봤다**. 그러면
#: 답변이 기록되지 않고(question_file_answers가 그 번호를 모른다) 화면의 문항 수·
#: answeredCount·진행률도 함께 어긋난다 — 2026-08-16 keumkang-v5의 결함이다.
#:
#: `\b`가 경계다: 번호가 있어야 문항이므로 `## Questions 개요`나 `## Questionnaire`
#: 는 걸리지 않는다. 그것까지 삼키면 카테고리 헤더가 한 문항으로 뭉개진다.
#:
#: **수식어 한 단어와 해시 4개를 허용한다.** 상류는 문항 헤딩을 한 형태로 쓰지
#: 않는다 — `question-format-guide.md`가 `## Question [Number]`(22행)와
#: `### Clarification Question 1`(223행, "Creating Clarification Questions")을
#: 모두 템플릿으로 싣고, 룰셋에는 `#### Question 1: Brand & Design Context`도 있다.
#: 2026-08-17 test-wf: `pain-point-clarification-questions.md`가 그 두 번째 형태를
#: 써서 **문항 0개**로 읽혔고, 그 파일의 답변은 기록되지 않았다.
#:
#: 수식어를 **한 단어로 제한하는 것이 핵심**이다. 임의 접두를 허용하면
#: `## Answer to Question 3` 같은 참조용 산문 헤딩까지 문항으로 잡혀 그 절 전체가
#: 한 문항으로 뭉개진다 — 위 `\b` 경계가 막는 것과 같은 실패다. 번호는 여전히
#: 유일한 판별자이므로 `### Question File Format`,
#: `### Context Questions (Per Use Case)`,
#: `### ⛔ GATE: Await PRFAQ Clarifying Question Answers`는 그대로 걸리지 않는다.
#:
#: `serialize_answers`가 같은 정규식을 쓴다(그 함수의 주석 참조) — 헤더 인식이
#: 곧 되기록이므로 두 경로가 갈라지면 "파싱은 되는데 답변이 안 써지는" 상태가 된다.
#:
#: **지역화된 헤딩도 읽는다(`## 질문 1`).** 2026-08-17 sarang-hpt: 완전히 정상인
#: 질문 파일이 문항 0개로 읽혀 카드가 뜨지 않았고, 다른 점은 헤딩 단어뿐이었다.
#: 원인은 규약 변경이다 — 질문 파일이 도구 호출의 사본이 아니라 사용자용 산출물이
#: 되면서 에이전트가 헤딩까지 프로젝트 언어로 옮겼다. AskUserQuestion이 거부되는
#: 지금 그 질문은 **완전히 사라진다.**
#:
#: 관용을 여기 두고 `## Question N`을 쓰라는 지시는 `discovery-config/CLAUDE.md`에
#: 둔다 — 상류 `question-format-guide.md`가 그 형식의 정본이고 건드리지 않는다.
#:
#: **허용목록이지 일반 규칙이 아니다.** "숫자로 끝나는 헤딩"처럼 느슨하게 하면
#: 실재하는 카테고리 헤딩을 삼킨다 — 명확화 질문 파일의 `## 모호성 1`이 그것이고,
#: 그것을 문항으로 삼으면 그 아래의 진짜 문항이 절 안으로 흡수된다. 프로젝트
#: 언어가 ko/en 둘뿐이므로(models·prompts의 `_LANGUAGES`) 목록으로 충분하다.
_QUESTION_WORDS = ("Question", "질문")
_Q_HEADER = re.compile(
    r"^#{2,4}\s+(?:\S+\s+)??(?:" + "|".join(_QUESTION_WORDS) + r")\s+(\d+)\b",
    re.MULTILINE)
_CAT_HEADER = re.compile(r"^##\s+(?!Question\b)(.+?)\s*$", re.MULTILINE)
_OPTION = re.compile(r"^([A-F]|X)\)\s+(.*)$")
_ANSWER = re.compile(r"^\[Answer\]:\s*(.*)$")
_RECO = re.compile(r"\s*←\s*(추천|recommended).*$", re.IGNORECASE)

def parse_question_file(name: str, markdown: str) -> QuestionFile:
    try:
        return _parse(name, markdown)
    except Exception:
        logger.warning("parse_question_file falling back to raw markdown for %s", name, exc_info=True)
        return QuestionFile(name=name, preamble=None, questions=[],
                            parse_ok=False, raw_markdown=markdown)

def _parse(name: str, markdown: str) -> QuestionFile:
    lines = markdown.splitlines()
    questions: list[Question] = []
    current_category: str | None = None
    preamble_lines: list[str] = []
    seen_first_header = False
    # 카테고리 헤더 뒤, 문항 헤더 앞의 최상위 산문. 다음 문항의 `context`가 된다.
    # 카테고리가 바뀌면 버려진다 — 그 산문은 이전 카테고리의 것이므로 다른
    # 카테고리의 문항에 붙이면 엉뚱한 설명이 달린다.
    #
    # 문항 블록 **안**은 아래 내부 루프가 다음 헤더까지 전부 소비하므로, 최상위
    # 산문은 카테고리 헤더 직후에만 생긴다. 그래서 이 버퍼는 대부분의 파일에서
    # 끝까지 비어 있고 기존 파싱 결과가 바뀌지 않는다.
    context_blocks: list[list[str]] = [[]]

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        qm = _Q_HEADER.match(line)
        cm = _CAT_HEADER.match(line)
        # Category header (## X, but not "## Question")
        if cm and not qm and line.startswith("## "):
            current_category = cm.group(1).strip()
            seen_first_header = True
            context_blocks = [[]]
            i += 1
            continue
        if qm:
            seen_first_header = True
            number = int(qm.group(1))
            i += 1
            # 블록 안은 `\n`으로 잇는다 — `text`/`ask`가 `" "`로 잇는 것과 다르다.
            # 그쪽은 유사도 비교에 쓰이지만 context는 **마크다운으로 렌더**되므로
            # 줄 구조가 의미를 갖는다. 실측: 확인 게이트 질문의 전제가 5행 표인데
            # 공백으로 이으면 `| # | … | |---|---| | 1 | …`이 되어 표가 아니게 된다.
            context = "\n\n".join("\n".join(b) for b in context_blocks if b).strip()
            context_blocks = [[]]
            # 문단 단위로 모은다. 마지막 문단이 실제 질문 문장이고(models.Question.ask
            # 참조) 그 앞은 메타·배경이다. 빈 줄이 문단 경계다.
            text_blocks: list[list[str]] = [[]]
            options: list[QuestionOption] = []
            answer: str | None = None
            # consume until next header
            while i < n and not _Q_HEADER.match(lines[i]) and not (
                lines[i].startswith("## ") and not _Q_HEADER.match(lines[i])
            ):
                raw = lines[i].strip()
                om = _OPTION.match(raw)
                am = _ANSWER.match(raw)
                if am:
                    answer = am.group(1).strip() or None
                elif om:
                    letter, otext = om.group(1), om.group(2).strip()
                    recommended = bool(_RECO.search(otext))
                    otext = _RECO.sub("", otext).strip()
                    options.append(QuestionOption(
                        letter=letter, text=otext,
                        is_other=(letter == "X" or otext.lower().startswith("other")),
                        recommended=recommended,
                    ))
                elif raw and not options:
                    text_blocks[-1].append(raw)
                elif not raw and text_blocks[-1] and not options:
                    # 빈 줄 = 문단 경계. 옵션이 시작된 뒤의 빈 줄은 무시한다.
                    text_blocks.append([])
                i += 1
            blocks = [b for b in text_blocks if b]
            questions.append(Question(
                number=number, category=current_category,
                text=" ".join(l for b in blocks for l in b).strip(),
                ask=" ".join(blocks[-1]).strip() if blocks else "",
                context=context,
                options=options, answer=answer,
            ))
            continue
        if not seen_first_header and line.strip():
            preamble_lines.append(line.rstrip())
        elif seen_first_header:
            # 첫 헤더 뒤의 최상위 산문 — 다음 문항의 context로 모은다. 빈 줄이
            # 문단 경계다(문항 본문과 같은 규칙).
            #
            # `rstrip`만 한다: 들여쓰기가 마크다운의 의미다(중첩 목록, 코드 블록).
            # preamble_lines도 같은 이유로 rstrip을 쓴다.
            kept = line.rstrip()
            if kept.strip() and kept.strip() != "---":
                context_blocks[-1].append(kept)
            elif not kept.strip() and context_blocks[-1]:
                context_blocks.append([])
        i += 1

    if not questions:
        raise ValueError("no questions found")
    preamble = "\n".join(preamble_lines).strip() or None
    return QuestionFile(name=name, preamble=preamble, questions=questions,
                        parse_ok=True, raw_markdown=None)

def serialize_answers(markdown: str, answers: dict[int, str]) -> str:
    present = {q.number for q in _parse("_", markdown).questions}
    missing = set(answers) - present
    if missing:
        raise KeyError(f"question numbers not in file: {sorted(missing)}")

    lines = markdown.splitlines(keepends=True)
    current_q: int | None = None
    out: list[str] = []
    for line in lines:
        # Match on the same basis _parse uses (the raw line minus its line
        # ending) so header detection can't diverge between the two passes.
        qm = _Q_HEADER.match(line.rstrip("\r\n"))
        stripped = line.strip()
        if qm:
            current_q = int(qm.group(1))
            out.append(line)
            continue
        if current_q in answers and stripped.startswith("[Answer]:"):
            m_end = re.search(r"(\r\n|\r|\n)$", line)
            ending = m_end.group(1) if m_end else ""
            out.append(f"[Answer]: {answers[current_q]}{ending}")
            continue
        out.append(line)
    return "".join(out)
