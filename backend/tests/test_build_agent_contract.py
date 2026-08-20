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

import aipds

#: 레포 루트. app._rules_dir()가 같은 방식으로 계산한다.
REPO = Path(aipds.__file__).resolve().parent.parent.parent
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
    있다. AI-PDS의 대응은 **TypeScript SDK**다 — 실측(2026-08-17):
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


# ---- 금지하면서 실행법을 가르치지 않는다 ----

#: 서버 기동 레시피의 흔적. `proto-config/CLAUDE.md`에 "If you really must start a
#: server" 절이 있었고, `setsid npm run start` / `kill -- -$PGID`를 그대로 적어
#: 뒀다 — 금지하는 문장 바로 아래에서.
SERVER_RECIPE = ("setsid", "PGID", "kill -- -", "smoke.log")


@pytest.mark.parametrize("needle", SERVER_RECIPE)
def test_no_recipe_for_starting_a_server(needle):
    """서버 기동은 이제 코드로 막힌다(proto/build_guard.py) — 레시피는 남아 있을
    이유가 없고, 남아 있으면 금지된 일의 실행 가능성만 높인다.

    `test_build_guard.test_wrapping_the_server_in_setsid_is_still_denied`가 그
    명령이 실제로 거부됨을 고정한다.
    """
    hits = [str(p.relative_to(REPO)) for p in _build_agent_files()
            if needle in _text(p)]
    assert not hits, (
        f"{needle!r} — 서버 기동 레시피가 남아 있다. 기동은 build_guard가 "
        f"거부하므로 이 문장은 우회법 교습일 뿐이다. 위치: {hits}")


def test_the_process_gate_is_advertised_as_enforced():
    """"금지"만 적으면 에이전트가 예외를 합리화한다.

    강제된다는 사실을 적어야 시도 자체가 줄고, 거부를 만났을 때 그것이 버그가
    아니라 규약임을 안다 — `discovery-config/CLAUDE.md`가 쓰기 범위 규칙에 대해
    같은 문장을 갖고 있다("This one is enforced, not trusted").
    """
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "enforced" in text.lower(), (
        "프로세스·포트 규칙이 강제된다는 표시가 없다 — build_guard가 실제로 "
        "막는데 계약이 그것을 말하지 않으면 에이전트는 산문으로 읽는다")


# ---- 공통 기술 계약의 SSOT는 이 파일이다 ----

#: `plan_prompt`에서 옮겨 온 규칙들. 언어별 프롬프트 2벌 + 계약 = 3벌이었고,
#: 그중 하나가 빠지는 것이 이 리포가 이미 기록해 둔 드리프트 경로다.
#: test_proto_prompts.test_the_plan_prompt_does_not_restate_the_shared_contract가
#: 프롬프트 쪽의 부재를 고정하고, 이 테스트가 계약 쪽의 존재를 고정한다 —
#: 한쪽만 있으면 규칙이 어디에도 없는 상태가 통과한다.
MOVED_FROM_THE_PLAN_PROMPT = {
    "prototype/": "산출물 위치",
    "README": "빌드·실행 설명 문서",
    "BEDROCK_MODEL_ID": "호스팅이 모델을 주입하는 환경변수 이름",
    "default credential chain": "API 키를 하드코딩하지 않는 근거",
    "basePath": "하위 경로 서빙",
    "@strands-agents/sdk": "agentic 스택",
    "BedrockModel": "샘플링 파라미터 금지가 Strands 생성자도 덮는다",
}


@pytest.mark.parametrize("needle,what",
                         sorted(MOVED_FROM_THE_PLAN_PROMPT.items()))
def test_the_contract_carries_what_the_plan_prompt_gave_up(needle, what):
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert needle in text, (
        f"{needle!r}({what})가 계약에 없다 — plan_prompt에서 뺐으므로 이제 "
        f"어디에도 없다")


# ---- README가 실제 배선과 일치한다 ----

def test_no_readme_promises_that_a_skill_activates_by_itself():
    """`skills="all"`은 2026-08-01 사고의 원인이었고 이미 되돌렸다.

    builder.py는 `skills=["shadcn-design"]`로 **이름 목록**을 넘긴다("all"은
    CLI 번들 스킬까지 켜고, 그 목록의 `run`이 브라우저를 띄워 프론트엔드를
    죽였다). 두 README는 "커밋한다. 끝 — 코드 변경이 필요 없다"고 안내하고
    있었다. 그 안내를 따르면 새 스킬이 **조용히 켜지지 않는다.**

    검사 대상은 그 **약속**이며 `skills="all"`이라는 말 자체가 아니다. 되돌린
    이유를 설명하려면 그 이름을 불러야 하고, README는 모델이 읽지 않으므로
    (`_build_agent_files`에 없다) 언급의 컨텍스트 비용도 없다.
    """
    stale = []
    for readme in (PROTO_CONFIG / "README.md", DISCOVERY_CONFIG / "README.md"):
        if not readme.is_file():
            continue
        if "코드 변경이 필요 없다" in _text(readme):
            stale.append(str(readme.relative_to(REPO)))
    assert not stale, (
        "스킬이 저절로 켜진다고 약속한다 — builder.py의 목록을 함께 고쳐야 "
        f"한다. 위치: {stale}")


def test_the_proto_readme_names_the_skill_that_is_actually_enabled():
    """새 스킬을 추가할 때 코드도 고쳐야 한다는 사실이 README에 있어야 한다 —
    "all"의 편의를 의도적으로 포기한 자리이므로(builder.py의 주석) 그 대가를
    문서가 말해야 한다."""
    text = _text(PROTO_CONFIG / "README.md")
    assert "shadcn-design" in text
    assert "builder.py" in text, (
        "스킬 목록을 어디서 고치는지 지목하지 않는다")


# ---- 브랜드 프로필: 두 디자인 권위의 우선순위 ----

def test_the_contract_names_the_brand_section_and_its_precedence():
    """프로필이 있는 프로젝트에서 빌드 에이전트는 디자인 권위를 **둘** 읽는다.

    "user" 레벨(`proto-config/CLAUDE.md`)은 shadcn-design 스킬의 기본 외형을,
    "project" 레벨(빌드 디렉터리 `CLAUDE.md`)은 `design_sync`가 심은 브랜드 절을
    지시한다. 어느 쪽이 이기는지 적혀 있지 않으면 결과가 근접성과 CSS 캐스케이드에
    맡겨진다 — 실제로는 맞게 나오지만 그것은 **우연히 맞는 것**이고 명시된 계약이
    아니다.

    이 사실은 **공유 사실**이다: "브랜드 절이 나타날 수 있고, 나타나면 그쪽이
    이긴다"는 모든 빌드에 대해 참이므로 프로젝트별 값이 아니다. 그래서 조건부
    문장으로 "user" 레벨에 둔다 — 프로필이 없는 빌드에는 아무 지시도 주지 않는다.
    """
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "shadcn-design" in text
    assert "DESIGN.md" in text, "브랜드 참고 문서를 지목하지 않는다"


def test_the_brand_marker_in_the_contract_is_the_one_design_sync_writes():
    """마커는 `design_sync`가 심는 것과 **같은 문자열**이어야 한다.

    이름이 바뀌면 계약의 지목이 조용히 낡는다 — 에이전트는 존재하지 않는 마커를
    찾고, 아무 에러도 나지 않는다. 그래서 상수를 직접 import해 대조한다.
    """
    from aipds.proto.design_sync import _SECTION_START
    assert _SECTION_START in _text(PROTO_CONFIG / "CLAUDE.md"), (
        f"{_SECTION_START!r}를 지목하지 않는다")


def test_the_contract_does_not_depend_on_the_localized_brand_heading():
    """헤딩은 지역화된다 — `## Brand design profile` / `## 브랜드 디자인 프로필`.

    공유 config는 언어 중립이어야 하므로(test_agent_language가 고정한다) 한쪽
    언어의 헤딩을 지목하면 다른 언어 프로젝트에서 찾을 수 없는 것을 가리킨다.
    언어와 무관한 식별자는 `design_sync`의 HTML 주석 마커뿐이다.
    """
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "Brand design profile" not in text, (
        "지역화되는 헤딩을 지목한다 — 마커를 쓸 것")
