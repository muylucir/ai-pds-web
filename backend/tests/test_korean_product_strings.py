# backend/tests/test_korean_product_strings.py
#
# **Keep the strings served to Korean projects from being translated away.**
#
# On 2026-08-21 we began moving comments and docstrings into English (to open the
# repo to the AI-PDS team). Comments reach nothing, so that is safe -- no code reads
# `__doc__` at runtime, and there is no Korean `HTTPException detail` either
# (measured). The risk is **touching string literals while translating**, and with
# Korean in 392 of 459 files, it cannot be caught by eye.
#
# Where a safety net already exists:
#   - the ko branches of `agent/prompts.py` and `proto/prompts.py`
#     -> test_agent_language.test_every_korean_prompt_is_korean_and_unknown_falls_back
#   - Korean matched by parsers (`## 질문 N`, `### 사용자 입력`)
#     -> tests/test_parse_questions.py, tests/test_parsers_audit.py
#   - `workspace_rules.LANGUAGE_DIRECTIVES["ko"]`
#     -> tests/test_workspace_rules.py
#
# **The places with no net are this file's target.** The three below are values shown
# as-is to Korean users or that instruct the model "한국어로 써라" (write in Korean),
# yet no test required that Korean -- spots where a translation accident could slip by
# silently.
from __future__ import annotations

import ast
import pathlib

import pytest

AIPDS = pathlib.Path(__file__).resolve().parents[1] / "aipds"


def _runtime_korean(rel: str) -> list[str]:
    """The module's **runtime** Korean strings (docstrings excluded).

    Excluding docstrings is the point: docstrings are subject to translation and string
    literals are not. Mixing the two would make this check block the translation.
    """
    tree = ast.parse((AIPDS / rel).read_text(encoding="utf-8"))
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docs.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs
                and any("가" <= c <= "힣" for c in node.value)):
            out.append(node.value)
    return out


#: (module, minimum count, what that Korean is). The count is a lower bound that catches
#: "wiped out by translation", not an exact count -- pinning it exactly would mean fixing
#: this test every time a string is added, and then people just bump the number and move on.
_PRODUCT_KOREAN = [
    ("survey/report_labels.py", 20,
     "설문 리포트의 한국어 라벨 — 한국어 프로젝트의 리포트에 그대로 찍힌다"),
    ("agent/questions_payload.py", 5,
     "보기 라벨('Other — 직접 입력' 등) — 질문 폼 화면에 그대로 뜬다"),
    ("survey/builder.py", 3,
     "설문 생성 프롬프트의 ko 갈래 — '문항·선택지를 모두 한국어로 써라'가 사라지면 "
     "한국어 프로젝트의 설문이 영어로 생성된다"),
]


@pytest.mark.parametrize("rel,minimum,why", _PRODUCT_KOREAN)
def test_product_korean_survives_a_comment_translation_sweep(rel, minimum, why):
    found = _runtime_korean(rel)
    assert len(found) >= minimum, (
        f"{rel}의 런타임 한국어가 {len(found)}개로 줄었다(하한 {minimum}). {why}. "
        f"주석·docstring 번역이 문자열 리터럴까지 건드렸는지 확인할 것.")


def test_the_survey_builder_still_orders_korean_output():
    """The instruction in `survey/builder.py` is the value in this file that breaks most quietly.

    Counting alone would pass even if this one line vanished while other Korean strings
    remain. So we check the **key words** of the instruction directly -- a survey being
    generated in English only surfaces after the user has distributed the survey.
    """
    joined = "\n".join(_runtime_korean("survey/builder.py"))
    # Counting only the word "한국어" (Korean) is not enough -- it appears twice in the
    # same string, so the check passes even if the **instruction** vanishes (measured:
    # the first version of this check did exactly that). Check the imperative phrase directly.
    assert "한국어로 써라" in joined, (
        "설문을 한국어로 쓰라는 **지시**가 없다 — 낱말만 남아 있어도 안 된다")
