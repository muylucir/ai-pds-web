# backend/tests/test_build_agent_contract.py
#
# Guards that what the build agent reads is **true**.
#
# Why this file exists (2026-08-17). `proto-config/skills/shadcn-design/` was copied as-is from
# another harness, so it cited authorities that do not exist in this repo: a static validator
# that does not exist (`check-markdown-render.mjs`, the `[J]` gate), sibling skills that do not
# exist (`cloudscape`, `api-contract-zod`), a numbered rule that does not exist
# (`Rule 9`), and, decisively, the Python premise **"implement the AI with Strands SDK + Bedrock"**.
# `ProtoHost` only runs the npm lifecycle (proto/host.py), so a Python prototype cannot be served,
# and `proto-config/CLAUDE.md` and `proto/prompts.py` in the same context direct server-side JS.
# The agent was receiving contradictory instructions at the same time, and that was the cause of
# "Strands prototype creation is broken".
#
# This test guards a single invariant: **every instruction in the build agent's context points
# only to what this repo actually provides.** False authority makes the agent try to honor a
# contract it cannot honor, and that failure leaves nothing in our logs.
from __future__ import annotations

import re
from pathlib import Path

import pytest

import aipds

#: Repo root. app._rules_dir() computes it the same way.
REPO = Path(aipds.__file__).resolve().parent.parent.parent
PROTO_CONFIG = REPO / "proto-config"
DISCOVERY_CONFIG = REPO / "discovery-config"

#: Every file the build agent actually reads: the contract plus the skills builder.py enables
#: by name (test_proto_builder pins that the list and proto-config/skills/ agree).
def _build_agent_files() -> list[Path]:
    files = [PROTO_CONFIG / "CLAUDE.md"]
    files += sorted((PROTO_CONFIG / "skills").rglob("*.md"))
    return [p for p in files if p.is_file()]


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_build_agent_reads_something():
    """Keeps the guards from passing on an empty list."""
    files = _build_agent_files()
    assert len(files) >= 2, [str(f) for f in files]
    assert (PROTO_CONFIG / "skills" / "shadcn-design" / "SKILL.md") in files
    assert (PROTO_CONFIG / "skills" / "antd-design" / "SKILL.md") in files


def test_the_contract_says_when_each_design_skill_applies():
    """With two design skills enabled, the contract must say which one a prototype uses.

    Without a rule the choice is left to whichever skill description reads closer, and an
    improvement session can switch systems halfway — the two have different tokens and reset
    CSS, and a mix of them breaks the screen."""
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "antd-design" in text and "shadcn-design" in text
    assert "package.json" in text, "개선 세션이 기존 디자인 시스템을 알아볼 근거가 없다"
    assert "default" in text, "어느 쪽이 기본인지 말하지 않는다"


def test_skill_references_point_at_files_that_exist():
    """A skill that names `references/x.md` sends the agent to read it; a missing file is
    an authority that does not exist."""
    missing = []
    for skill in sorted((PROTO_CONFIG / "skills").glob("*/SKILL.md")):
        for p in [skill, *skill.parent.rglob("references/*.md")]:
            for rel in set(re.findall(r"`(references/[a-z0-9-]+\.md)`", _text(p))):
                if not (skill.parent / rel).is_file():
                    missing.append(f"{p.relative_to(REPO)} → {rel}")
    assert not missing, missing


def _shared_streaming_patterns(skill: str) -> str:
    """Patterns 0 and 1 (server route, streaming hook) of a skill's ai-streaming.md."""
    text = _text(PROTO_CONFIG / "skills" / skill / "references" / "ai-streaming.md")
    start = text.index("## 패턴 0:")
    return text[start:text.index("## 패턴 2:", start)]


def test_both_design_skills_carry_the_same_streaming_wiring():
    """The server route and the hook do not depend on the design system, and each skill
    carries its own copy so a build reads only the skill it uses (pointing antd-design at
    shadcn-design's file made an antd build load shadcn's rendering too). Two copies drift —
    the event mapping in them is measured, and a fix landing in one would leave the other
    streaming tool-input JSON into the chat. So they must stay identical."""
    assert (_shared_streaming_patterns("antd-design")
            == _shared_streaming_patterns("shadcn-design"))


def test_the_contract_pins_typescript_5():
    """Measured 2026-10-28: with an unpinned `typescript` the install resolved to 7.0.2, and
    `next build` (15.5) failed with `Module not found` on every `@/…` import while the files
    were there; pinning `typescript@5` alone made the same tree build. Without the pin in the
    contract the agent installs the latest and reads the failure as a missing file."""
    assert "typescript@5" in _text(PROTO_CONFIG / "CLAUDE.md")


# ---- Do not cite authorities that do not exist ----

#: Things this repo does not have. The value is "why it is absent".
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
    """The `[J]` gate does not exist in this harness.

    A sentence like "the gate enforces this statically" makes the agent believe **there is
    validation**. In reality nothing checks it, so the instruction is labeled as enforced while
    it is not enforced — and there is no signal when it is not followed."""
    gate = re.compile(r"\[J\]")
    hits = [str(p.relative_to(REPO)) for p in _build_agent_files()
            if gate.search(_text(p))]
    assert not hits, f"`[J]` 게이트를 인용한다: {hits}"


def test_no_reference_to_a_numbered_rule():
    """There are no numbered rules such as `Rule 9` or `CLAUDE.md Rule 6/7`.

    proto-config/CLAUDE.md is organized into titled sections, not numbers. Citing a number makes
    the agent look for a rule it cannot find."""
    numbered = re.compile(r"\bRule \d")
    hits = [str(p.relative_to(REPO)) for p in _build_agent_files()
            if numbered.search(_text(p))]
    assert not hits, f"번호 규칙을 인용한다: {hits}"


# ---- Do not direct a stack that contradicts the hosting contract ----

#: All ProtoHost runs is `npm install` -> `npm run build` -> `npm run start`
#: (proto/host.py). Directing a Python runtime means that even if it is built, it is not served.
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
    """The stack for agentic prototypes must be stated explicitly.

    The upstream rule (`prototype-building.md`) requires Strands but is written for Python.
    AI-PDS's counterpart is the **TypeScript SDK** — measured (2026-08-17):
    `@strands-agents/sdk` 1.13.0 depends directly on `@aws-sdk/client-bedrock-runtime`, and the
    Bedrock default credential chain + tools + streaming work on Node 20. Without the name written
    down, the agent goes looking for the Python SDK."""
    joined = "\n".join(_text(p) for p in _build_agent_files())
    assert "@strands-agents/sdk" in joined, (
        "agentic 프로토타입이 쓸 패키지 이름이 어디에도 없다")


def test_the_sampling_parameter_rule_covers_the_strands_constructor():
    """The `temperature` ban must cover the Strands constructor, not just Converse.

    Measured (2026-08-17): `new BedrockModel({ temperature: 0.7 })` fails with
    `ModelError: \\`temperature\\` is deprecated for this model` — and that value is
    **verbatim from the example** in the SDK README. If the rule only mentions Converse's
    `inferenceConfig`, every agentic prototype breaks the moment the agent copies the README."""
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "BedrockModel" in text, (
        "Strands의 BedrockModel 생성자를 지목하지 않는다 — README 예제가 "
        "temperature를 넣으므로 Converse만 막으면 새어 나간다")


# ---- Discovery does not put a Python premise into the spec ----

def test_discovery_override_pins_the_prototype_runtime():
    """The runtime must be pinned at the spec stage.

    Discovery writes the spec having read the upstream `prototype-building.md` (pip/venv/flask).
    Without a runtime rule, the Python premise gets into the spec, and that spec is handed
    straight to the builder. The spec is a portable artifact (it may be handed to a Kiro-path
    team), so this rule must live in the override section — not inside a ruleset file."""
    text = _text(DISCOVERY_CONFIG / "CLAUDE.md")
    assert "Node" in text, "프로토타입 런타임이 Node라는 사실이 없다"
    assert "@strands-agents/sdk" in text, (
        "agentic 스펙이 가리켜야 할 SDK 이름이 없다")


# ---- Do not teach how to run something while forbidding it ----

#: Traces of a server-start recipe. `proto-config/CLAUDE.md` had an "If you really must start a
#: server" section that spelled out `setsid npm run start` / `kill -- -$PGID` verbatim —
#: right below the sentence forbidding it.
SERVER_RECIPE = ("setsid", "PGID", "kill -- -", "smoke.log")


@pytest.mark.parametrize("needle", SERVER_RECIPE)
def test_no_recipe_for_starting_a_server(needle):
    """Starting a server is now blocked in code (proto/build_guard.py) — there is no reason
    for the recipe to remain, and if it remains it only makes the forbidden act more feasible.

    `test_build_guard.test_wrapping_the_server_in_setsid_is_still_denied` pins that the
    command is actually denied.
    """
    hits = [str(p.relative_to(REPO)) for p in _build_agent_files()
            if needle in _text(p)]
    assert not hits, (
        f"{needle!r} — 서버 기동 레시피가 남아 있다. 기동은 build_guard가 "
        f"거부하므로 이 문장은 우회법 교습일 뿐이다. 위치: {hits}")


def test_the_process_gate_is_advertised_as_enforced():
    """If it only says "forbidden", the agent rationalizes exceptions.

    Stating that it is enforced reduces attempts in the first place, and when the agent hits a
    denial it knows that it is a convention, not a bug — `discovery-config/CLAUDE.md` has the
    same sentence for its write-scope rule ("This one is enforced, not trusted").
    """
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "enforced" in text.lower(), (
        "프로세스·포트 규칙이 강제된다는 표시가 없다 — build_guard가 실제로 "
        "막는데 계약이 그것을 말하지 않으면 에이전트는 산문으로 읽는다")


# ---- This file is the SSOT for the shared technical contract ----

#: Rules moved over from `plan_prompt`. There were 3 copies: 2 per-language prompts + the contract,
#: and one of them going missing is a drift path this repo has already recorded.
#: test_proto_prompts.test_the_plan_prompt_does_not_restate_the_shared_contract pins
#: their absence on the prompt side, and this test pins their presence on the contract side —
#: with only one of them, a state where the rule exists nowhere would pass.
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


# ---- The READMEs match the actual wiring ----

def test_no_readme_promises_that_a_skill_activates_by_itself():
    """`skills="all"` caused the 2026-08-01 incident and has already been reverted.

    builder.py passes a **list of names** with `skills=["shadcn-design"]` ("all" also enabled
    the CLI-bundled skills, and the `run` in that list launched a browser and killed the
    frontend). Both READMEs were saying "commit it. Done — no code change needed". Following
    that guidance means a new skill **silently does not get enabled.**

    What is checked is that **promise**, not the words `skills="all"` themselves. Explaining
    why it was reverted requires naming it, and the model does not read the READMEs
    (they are not in `_build_agent_files`), so mentioning it costs no context either.
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
    """The README must state that adding a new skill also requires a code change —
    this is where the convenience of "all" was deliberately given up (see the comment in
    builder.py), so the docs must state the price."""
    text = _text(PROTO_CONFIG / "README.md")
    assert "shadcn-design" in text
    assert "builder.py" in text, (
        "스킬 목록을 어디서 고치는지 지목하지 않는다")


# ---- Brand profile: precedence between two design authorities ----

def test_the_contract_names_the_brand_section_and_its_precedence():
    """In a project with a profile, the build agent reads **two** design authorities.

    The "user" level (`proto-config/CLAUDE.md`) directs the shadcn-design skill's default look,
    and the "project" level (the build directory's `CLAUDE.md`) directs the brand section that
    `design_sync` plants. If which one wins is not written down, the outcome is left to proximity
    and the CSS cascade — in practice it comes out right, but that is **right by accident**, not a
    stated contract.

    This is a **shared fact**: "a brand section may appear, and if it does, it wins" is true for
    every build, so it is not a per-project value. That is why it sits at the "user" level as a
    conditional sentence — it gives no instruction at all to builds without a profile.
    """
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "shadcn-design" in text
    assert "DESIGN.md" in text, "브랜드 참고 문서를 지목하지 않는다"


def test_the_brand_marker_in_the_contract_is_the_one_design_sync_writes():
    """The marker must be **the same string** that `design_sync` plants.

    If the name changes, the contract's reference silently goes stale — the agent looks for a
    marker that does not exist, and no error is raised. So the constant is imported directly and compared.
    """
    from aipds.proto.design_sync import _SECTION_START
    assert _SECTION_START in _text(PROTO_CONFIG / "CLAUDE.md"), (
        f"{_SECTION_START!r}를 지목하지 않는다")


def test_the_contract_does_not_depend_on_the_localized_brand_heading():
    """Headings are localized — `## Brand design profile` / `## 브랜드 디자인 프로필`.

    Shared config must be language-neutral (test_agent_language pins this), so naming one
    language's heading points to something that cannot be found in projects in the other language.
    The only language-independent identifier is `design_sync`'s HTML comment marker.
    """
    text = _text(PROTO_CONFIG / "CLAUDE.md")
    assert "Brand design profile" not in text, (
        "지역화되는 헤딩을 지목한다 — 마커를 쓸 것")
