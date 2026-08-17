# backend/tests/test_build_agent_contract.py
#
# 빌드 에이전트가 읽는 것이 **참인지** 지킨다.
#
# 왜 이 파일이 생겼는가(2026-08-17). `proto-config/skills/shadcn-design/`는 다른
# 하네스에서 그대로 가져온 것이어서, 이 레포에 없는 권위를 인용하고 있었다:
# 존재하지 않는 정적 검증기(`check-markdown-render.mjs`, `[J]` 게이트), 존재하지
# 않는 형제 스킬(`cloudscape`, `api-contract-zod`), 존재하지 않는 번호 규칙
# (`Rule 9`), 그리고 결정적으로 **"AI를 Strands SDK + Bedrock으로 구현하고"**라는
# 파이썬 전제. `ProtoHost`는 npm 라이프사이클만 돌리므로(proto/host.py) 파이썬
# 프로토타입은 서빙될 수 없고, 같은 컨텍스트의 `proto-config/CLAUDE.md`와
# `proto/prompts.py`는 서버측 JS를 지시한다. 에이전트가 상반된 지시를 동시에
# 받는 상태였고, 그것이 "Strands prototype creation is broken"의 원인이다.
#
# 이 테스트가 지키는 불변식은 하나다: **빌드 에이전트의 컨텍스트에 있는 모든
# 지시는 이 레포가 실제로 제공하는 것만 가리킨다.** 거짓 권위는 에이전트가 지킬
# 수 없는 계약을 지키려 하게 만들고, 그 실패는 우리 로그에 아무것도 남기지
# 않는다.
from __future__ import annotations

import re
from pathlib import Path

import pytest

import pathfinder

#: 레포 루트. app._rules_dir()가 같은 방식으로 계산한다.
REPO = Path(pathfinder.__file__).resolve().parent.parent.parent
PROTO_CONFIG = REPO / "proto-config"
DISCOVERY_CONFIG = REPO / "discovery-config"

#: 빌드 에이전트가 실제로 읽는 파일 전부. 스킬은 builder.py가
#: `skills=["shadcn-design"]`로 켠 것 하나뿐이다.
def _build_agent_files() -> list[Path]:
    files = [PROTO_CONFIG / "CLAUDE.md"]
    files += sorted((PROTO_CONFIG / "skills").rglob("*.md"))
    return [p for p in files if p.is_file()]


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_build_agent_reads_something():
    """가드가 빈 목록을 통과하지 않게 한다."""
    files = _build_agent_files()
    assert len(files) >= 2, [str(f) for f in files]
    assert (PROTO_CONFIG / "skills" / "shadcn-design" / "SKILL.md") in files


# ---- 존재하지 않는 권위를 인용하지 않는다 ----

#: 이 레포에 없는 것들. 값은 "왜 없는가"다.
ABSENT_AUTHORITIES = {
    "check-markdown-render.mjs": "정적 검증기가 이 레포에 없다",
    "sub-check": "sub-check 체계가 이 레포에 없다",
    "cloudscape 스킬": "형제 스킬이 배포되지 않는다(빌더는 shadcn-design만 켠다)",
    "api-contract-zod": "그 이름의 스킬이 이 레포에 없다",
}


@pytest.mark.parametrize("needle,reason", sorted(ABSENT_AUTHORITIES.items()))
def test_no_reference_to_an_absent_authority(needle, reason):
    hits = [f"{p.relative_to(REPO)}" for p in _build_agent_files()
            if needle in _text(p)]
    assert not hits, f"{needle!r}를 인용한다 — {reason}. 위치: {hits}"


def test_no_reference_to_a_static_gate_that_does_not_run():
    """`[J]` 게이트는 이 하네스에 없다.

    "게이트가 정적으로 강제한다"는 문장은 에이전트에게 **검증이 있다**고
    믿게 만든다. 실제로는 아무것도 검사하지 않으므로, 지시는 강제되지 않는
    채로 강제된다고 표시된다 — 지키지 않아도 아무 신호가 없다."""
    gate = re.compile(r"\[J\]")
    hits = [str(p.relative_to(REPO)) for p in _build_agent_files()
            if gate.search(_text(p))]
    assert not hits, f"`[J]` 게이트를 인용한다: {hits}"


def test_no_reference_to_a_numbered_rule():
    """`Rule 9`, `CLAUDE.md Rule 6/7` 같은 번호 규칙은 없다.

    proto-config/CLAUDE.md는 번호가 아니라 제목 절로 구성돼 있다. 번호를
    인용하면 에이전트가 찾을 수 없는 규칙을 찾는다."""
    numbered = re.compile(r"\bRule \d")
    hits = [str(p.relative_to(REPO)) for p in _build_agent_files()
            if numbered.search(_text(p))]
    assert not hits, f"번호 규칙을 인용한다: {hits}"


# ---- 호스팅 계약과 어긋나는 스택을 지시하지 않는다 ----

#: ProtoHost가 실행하는 것은 `npm install` -> `npm run build` -> `npm run start`
#: 뿐이다(proto/host.py). 파이썬 런타임을 지시하면 만들어져도 서빙되지 않는다.
PYTHON_RUNTIME = ("pip install", "python -m venv", "flask", "uvicorn",
                  "strands-agents==")


@pytest.mark.parametrize("needle", PYTHON_RUNTIME)
def test_no_python_runtime_instruction(needle):
    hits = [str(p.relative_to(REPO)) for p in _build_agent_files()
            if needle.lower() in _text(p).lower()]
    assert not hits, (
        f"{needle!r}를 지시한다 — ProtoHost는 npm 라이프사이클만 돌린다"
        f"(proto/host.py). 위치: {hits}")


def test_the_agentic_stack_is_named_and_is_the_typescript_sdk():
    """agentic 프로토타입의 스택이 명시돼 있어야 한다.

    상류 룰(`prototype-building.md`)은 Strands를 요구하는데 파이썬으로 쓰여
    있다. Pathfinder의 대응은 **TypeScript SDK**다 — 실측(2026-08-17):
    `@strands-agents/sdk` 1.13.0이 `@aws-sdk/client-bedrock-runtime`을 직접
    의존하고, Bedrock 기본 자격증명 체인 + 도구 + 스트리밍이 Node 20에서
    동작한다. 이름을 적어 두지 않으면 에이전트가 파이썬 SDK를 찾는다."""
    joined = "\n".join(_text(p) for p in _build_agent_files())
    assert "@strands-agents/sdk" in joined, (
        "agentic 프로토타입이 쓸 패키지 이름이 어디에도 없다")


def test_the_sampling_parameter_rule_covers_the_strands_constructor():
    """`temperature` 금지는 Converse뿐 아니라 Strands 생성자도 덮어야 한다.

    실측(2026-08-17): `new BedrockModel({ temperature: 0.7 })`은
    `ModelError: \\`temperature\\` is deprecated for this model`로 실패한다 —
    그리고 그 값은 SDK README의 **예제 그대로**다. 규칙이 Converse의
    `inferenceConfig`만 말하면, 에이전트가 README를 베끼는 순간 모든 agentic
    프로토타입이 깨진다."""
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "BedrockModel" in text, (
        "Strands의 BedrockModel 생성자를 지목하지 않는다 — README 예제가 "
        "temperature를 넣으므로 Converse만 막으면 새어 나간다")


# ---- Discovery가 스펙에 파이썬 전제를 넣지 않는다 ----

def test_discovery_override_pins_the_prototype_runtime():
    """스펙 단계에서 런타임을 못 박아야 한다.

    Discovery는 상류 `prototype-building.md`(pip/venv/flask)를 읽은 상태로
    스펙을 쓴다. 런타임 규정이 없으면 파이썬 전제가 스펙에 들어가고, 그 스펙이
    빌더에게 그대로 넘어간다. 스펙은 포터블 산출물이므로(Kiro 경로 팀에 넘어갈
    수 있다) 이 규정은 오버라이드 절에 있어야 한다 — 룰셋 파일 안이 아니다."""
    text = _text(DISCOVERY_CONFIG / "CLAUDE.md")
    assert "Node" in text, "프로토타입 런타임이 Node라는 사실이 없다"
    assert "@strands-agents/sdk" in text, (
        "agentic 스펙이 가리켜야 할 SDK 이름이 없다")
