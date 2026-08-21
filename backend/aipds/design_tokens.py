"""산문뿐인 DESIGN.md에서 디자인 토큰을 뽑는다.

**왜 있는가(2026-08-19 실측).** ```tokens 펜스는 우리 서식(`design_profile.TEMPLATE_MD`)
에만 있는 관례다. 밖에서 만들어진 DESIGN.md에는 없고, 그게 실제 입력이다 — 배포된
프로필(37KB 디자인 시스템 문서)은 역할→색 매핑을 산문과 표에 다 갖췄는데 펜스가
없어서 `tokens={}`가 됐고, 생성된 테마 CSS가 변수 0개로 나갔다. 정보가 없어서가
아니라 파서가 읽는 자리에 없어서 실패했다.

**이 모듈은 파서를 갖지 않는다.** 모델의 응답을 `parse_design_md`에 그대로 먹인다
(`_FENCE_OPEN`이 텍스트 어디서든 첫 펜스를 찾으므로 모델이 말을 덧붙여도 파싱된다).
그래서 hex·길이·서체 검증과 화이트리스트가 사람이 쓴 경우와 **같은 코드**를 지나고,
파서를 고치면 이 경로도 함께 따라온다. 응답용 파서를 따로 두면 그 두 벌이 어긋나고,
어긋난 쪽이 조용한 쪽이 된다.

S3도 FastAPI도 모른다 — 주입받는 것은 `async (prompt) -> str` 하나다
(`app.questionnaire_agent_factory`가 이미 만드는 모양).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from aipds.design_profile import (ALLOWED_TOKENS, COLOR_TOKENS,
                                       FONT_TOKENS, LENGTH_TOKENS,
                                       DesignProfileError, parse_design_md)

_log = logging.getLogger(__name__)

#: 프롬프트를 받아 응답 문자열을 돌려주는 단발 호출자.
Caller = Callable[[str], Awaitable[str]]

_FENCE = "```tokens"

#: 첫 시도 + 되묻기 한 번. 더 늘리지 않는 이유는 이 호출이 관리자의 업로드
#: 요청을 붙잡고 있기 때문이다 — 실패는 0토큰 경고로 내려가고, 그 상태도 유효하다.
_MAX_ATTEMPTS = 2


def has_fence(text: str) -> bool:
    return any(line.strip() == _FENCE
               for line in text.replace("\r\n", "\n").split("\n"))


def render_fence(tokens: dict[str, str]) -> str:
    """토큰을 ```tokens 블록으로. `parse_design_md`가 되읽을 수 있어야 한다."""
    lines = [_FENCE] + [f"{k}: {v}" for k, v in tokens.items()] + ["```"]
    return "\n".join(lines) + "\n"


def inject_fence(markdown: str, tokens: dict[str, str]) -> str:
    """원문에 블록을 심는다 — 첫 헤딩 바로 뒤, 헤딩이 없으면 맨 앞.

    저장물을 "원문 markdown + 메타" 하나로 유지하는 방법이다(파생값을 따로
    저장하지 않는다 — design_profile.py 모듈 docstring). 내려받은 파일에서
    관리자가 바로 보고 고칠 수 있는 자리이기도 하다.

    **산문은 한 바이트도 바뀌지 않는다.** 그래서 빈 줄을 덧붙이지 않고 헤딩
    다음 줄에 바로 붙인다 — 파서는 펜스 줄만 걷어내고 나머지를 이어 붙이므로,
    여기서 빈 줄을 하나 넣으면 그 줄이 산문 가운데 남는다(실측: 이 조건을
    시험이 잡았다).
    """
    fence = render_fence(tokens)
    lines = markdown.replace("\r\n", "\n").split("\n")
    idx = next((i for i, line in enumerate(lines) if line.startswith("#")), None)
    if idx is None:
        return fence + markdown
    return "\n".join(lines[:idx + 1]) + "\n" + fence + "\n".join(lines[idx + 1:])


def _value_rules() -> str:
    """값 서식 설명을 화이트리스트에서 만든다 — 토큰 그룹이 바뀌면 프롬프트가
    함께 바뀐다. 손으로 적으면 그 문장이 두 번째 진실이 되고 조용히 낡는다."""
    return "\n".join([
        f"- colours ({', '.join(COLOR_TOKENS)}): `#rrggbb` or `#rgb` only.",
        f"- lengths ({', '.join(LENGTH_TOKENS)}): a single value like "
        f"`0.75rem` or `12px`.",
        f"- fonts ({', '.join(FONT_TOKENS)}): a font family name.",
    ])


def _prompt(markdown: str) -> str:
    """추출 지시. 영어로 쓴다 — 이 출력은 사람이 읽는 문장이 아니라 기계가 되읽는
    블록이고, 입력 문서의 언어는 무엇이든 될 수 있다."""
    return f"""You are extracting design tokens from a brand design document.

Return ONE fenced block and nothing else that looks like one:

```tokens
key: value
```

Rules:
- Allowed keys, and no others: {', '.join(ALLOWED_TOKENS)}
{_value_rules()}
- **A value that is not already in the accepted form above is omitted, never
  converted.** The accepted forms are the whole of what this format can carry, so
  anything else — a colour written some other way, a value built from a function
  or a variable, a gradient, a list — is simply left out. Do not translate it and
  do not approximate it.
- **Follow the ROLE the document gives each value.** Read how the document itself
  labels and uses a value; its own naming is the authority. A document that calls
  something its primary colour is telling you `primary`; a document that instead
  names a colour by where it is used is telling you that use.
- **Where the document gives one role several values, omit that key.** This is
  the honest answer whenever the document does not resolve it — several corner
  radii for different components with none stated as the general rule, two
  surface colours alternating with neither called the default. Omit rather than
  pick.
- Omit any key the document does not answer. Omitted keys keep the shadcn
  default, which is the correct outcome — never invent a value to fill a slot.
- **Never move a colour from one role into another to fill a gap.** If a role's
  value cannot be expressed here, omit that key. Substituting a different colour,
  such as the brand colour, is worse than omitting: `foreground` is the colour of
  every line of text on the screen, so a brand colour there tints the whole UI.
- Set `font_mono` only if the document names a font for code or monospaced text.
  A display or handwriting font is not a monospace font.
- Where a font is named that a browser will not have — an in-house or licensed
  family — use the fallback the document gives for it. If it gives none, omit.
- Take a value from a font stack or a variable definition only when it is stated
  plainly enough to copy; the first named family of a stack is such a value.

The document follows.

---

{markdown}"""


def _retry_prompt(markdown: str, reply: str, problem: str) -> str:
    """되묻기. 파서가 짚어준 문장을 그대로 싣는다 — 줄 번호가 박힌 그 문장이
    모델이 스스로 고칠 수 있는 유일한 근거다."""
    return (f"{_prompt(markdown)}\n\n---\n\nYour previous answer was rejected.\n\n"
            f"You answered:\n{reply}\n\nThe parser said: {problem}\n\n"
            "Return the corrected block only.")


async def extract_tokens(
        markdown: str,
        call: Caller | None) -> tuple[dict[str, str], list[str]]:
    """(tokens, warnings). 예외를 올리지 않는다 — 추출 실패가 업로드를 막으면 안 된다.

    원문에 펜스가 있으면 **모델을 부르지 않는다**: 펜스가 권위이고, 손으로 쓴
    관리자의 경로는 종전과 한 글자도 다르지 않아야 한다. 그 경로의
    `DesignProfileError`는 그대로 올라간다(라우트가 400으로 번역한다).

    `call`이 None이면(배포에 ANTHROPIC_MODEL이 없다) 추출을 건너뛰고 경고만
    돌려준다 — 산문만 적용하는 것도 유효한 상태다.
    """
    if has_fence(markdown):
        return parse_design_md(markdown)[0], []
    if call is None:
        return {}, ["no model is configured, so no tokens could be read "
                    "from the document"]

    prompt = _prompt(markdown)
    problem = "no attempt was made"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        reply = await call(prompt)
        # 펜스가 없는 응답을 파서에 맡기면 ({}, 전체 산문)이 조용히 돌아온다 —
        # "추출 성공, 토큰 0개"로 읽혀서 지금 고치는 버그가 그대로 재현된다.
        if not has_fence(reply):
            problem = "the reply contained no ```tokens block"
        else:
            try:
                tokens, _ = parse_design_md(reply)
            except DesignProfileError as exc:
                problem = str(exc)
            else:
                if tokens:
                    return tokens, []
                problem = "the ```tokens block was empty"
        _log.warning("design token extraction rejected (attempt %d/%d): %s",
                     attempt, _MAX_ATTEMPTS, problem)
        prompt = _retry_prompt(markdown, reply, problem)

    return {}, [f"could not read design tokens from the document: {problem}"]
