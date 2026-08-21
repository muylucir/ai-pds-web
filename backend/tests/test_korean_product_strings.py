# backend/tests/test_korean_product_strings.py
#
# **한국어 프로젝트에 제공되는 문자열이 번역돼 사라지지 않게 한다.**
#
# 2026-08-21에 주석·docstring을 영어로 옮기는 작업을 시작했다(리포를 AI-PDS 팀에
# 열기 위해서다). 주석은 어디에도 닿지 않으므로 안전하다 — 런타임에 `__doc__`를 읽는
# 코드가 없고, 한글 `HTTPException detail`도 없다(실측). 위험은 **번역하다 문자열
# 리터럴을 건드리는 것**이고, 파일 459개 중 392개에 한글이 있어 눈으로는 못 잡는다.
#
# 그물이 이미 있는 곳:
#   - `agent/prompts.py`·`proto/prompts.py`의 ko 갈래
#     -> test_agent_language.test_every_korean_prompt_is_korean_and_unknown_falls_back
#   - 파서가 매칭하는 한국어(`## 질문 N`, `### 사용자 입력`)
#     -> tests/test_parse_questions.py, tests/test_parsers_audit.py
#   - `workspace_rules.LANGUAGE_DIRECTIVES["ko"]`
#     -> tests/test_workspace_rules.py
#
# **그물이 없던 곳이 이 파일의 대상이다.** 아래 셋은 한국어 사용자에게 그대로 보이거나
# 모델에게 "한국어로 써라"라고 지시하는 값인데, 어떤 테스트도 그 한국어를 요구하지
# 않았다 — 번역 사고가 조용히 지나갈 수 있는 자리였다.
from __future__ import annotations

import ast
import pathlib

import pytest

AIPDS = pathlib.Path(__file__).resolve().parents[1] / "aipds"


def _runtime_korean(rel: str) -> list[str]:
    """그 모듈의 **런타임** 한국어 문자열(docstring 제외).

    docstring을 제외하는 것이 요점이다: docstring은 번역 대상이고 문자열 리터럴은
    아니다. 둘을 섞으면 이 검사가 번역을 막아 버린다.
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


#: (모듈, 최소 개수, 그 한국어가 무엇인가). 개수는 "번역으로 싹 사라지는 것"을 잡는
#: 하한이지 정확한 카운트가 아니다 — 정확히 고정하면 문구를 하나 늘릴 때마다 이
#: 테스트를 고쳐야 하고, 그러면 사람이 숫자만 올리고 지나간다.
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
    """`survey/builder.py`의 지시가 이 파일에서 가장 조용히 깨지는 값이다.

    개수만 세면 다른 한국어 문자열이 남아 있는 동안 이 한 줄이 사라져도 통과한다.
    그래서 지시의 **핵심 낱말**을 직접 본다 — 설문이 영어로 생성되는 것은 사용자가
    설문을 배포한 뒤에야 드러난다.
    """
    joined = "\n".join(_runtime_korean("survey/builder.py"))
    # 낱말 "한국어"만 세면 안 된다 — 같은 문자열 안에 두 번 나오므로 **지시**가
    # 사라져도 통과한다(실측: 처음 쓴 판정이 그랬다). 명령형 구를 직접 본다.
    assert "한국어로 써라" in joined, (
        "설문을 한국어로 쓰라는 **지시**가 없다 — 낱말만 남아 있어도 안 된다")
