# backend/tests/test_agent_language.py
#
# 이 파일이 지키는 불변식은 하나다: **영어 프로젝트의 에이전트 컨텍스트에는
# 한글이 없다.**
#
# 왜 새 파일인가. 스펙 2026-08-03-bilingual-ko-en의 테스트
# (test_workspace_rules.py)는 "언어 지시가 하나이고 앞에 온다"를 검증했고 전부
# 통과했다. 그런데 2026-08-04에 영어를 고른 프로젝트의 워크스페이스 채팅이
# 한국어로 진행됐다. 그 테스트들이 못 잡은 이유는 검사 대상이 **지시**였기
# 때문이다 — 새는 곳은 지시가 아닌 텍스트였다:
#
#   1. 공유 CLAUDE_CONFIG_DIR의 CLAUDE.md 전문(한국어 산문 1935자). 전
#      프로젝트가 공유하므로 언어를 담을 수 없는데도 한국어였다.
#   2. MCP 도구 설명 — 모델이 매 턴 도구 목록과 함께 읽는다.
#   3. 도구 반환 문자열과 드라이버가 만드는 모델 대상 프롬프트.
#
# 그래서 여기서는 조립 순서가 아니라 **글자**를 본다. 한글 코드포인트가 하나라도
# 있으면 실패다. 이 판정은 둔하지만 정확히 그 점이 장점이다 — 다음에 누가 어떤
# 새 채널로 한국어를 흘리든, 그 채널이 이 검사를 통과하려면 언어를 받아야 한다.
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aipds.agent import prompts
from aipds.agent.workspace_rules import place_rules
from aipds.proto import prompts as proto_prompts
from aipds.proto.tools import build_proto_tools


REPO = Path(__file__).resolve().parents[2]


def hangul(text: str) -> list[str]:
    """텍스트에 있는 한글 음절을 중복 없이 돌려준다(빈 리스트 = 한글 없음).

    자모(ㄱ-ㅣ)까지 넣지 않는 이유: 화살표나 기호와 달리 실제 산문에는 완성형
    음절만 나온다. 완성형만 봐도 산문 한 줄을 놓치지 않는다.
    """
    return sorted({c for c in text if "가" <= c <= "힣"})


# ---- ① 워크스페이스 CLAUDE.md (place_rules의 조립물) ----


def test_english_workspace_rules_have_no_korean():
    """조립된 워크스페이스 CLAUDE.md 전문에 한글이 없어야 한다.

    test_workspace_rules.py는 "KO-DIRECTIVE가 없다"만 본다(픽스처가 플레이스홀더
    이므로 그 이상 볼 수 없다). 여기서는 실제 리포 룰로 조립해 글자를 센다 —
    상류 룰이나 language/en.md에 한국어가 섞여 들어오는 것까지 잡는다.
    """
    rules = REPO / "steering-files" / "aiplc-rules"
    if not (rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        # steering-files/는 서브모듈이다 — 초기화하지 않은 트리에서는 비어 있고,
        # 그러면 이 검사가 조용히 건너뛰어진다. `git submodule update --init`.
        pytest.skip("steering-files/ submodule not initialised")
    with tempfile.TemporaryDirectory() as ws:
        place_rules(ws, str(rules), language="en")
        text = (Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
    assert not hangul(text), (
        f"영어 프로젝트의 워크스페이스 CLAUDE.md에 한글이 있다: {hangul(text)[:12]}")


def test_korean_workspace_rules_still_carry_the_korean_directive():
    """대칭 확인 — en을 고치면서 ko를 비워버리는 회귀를 막는다."""
    rules = REPO / "steering-files" / "aiplc-rules"
    if not (rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        # steering-files/는 서브모듈이다 — 초기화하지 않은 트리에서는 비어 있고,
        # 그러면 이 검사가 조용히 건너뛰어진다. `git submodule update --init`.
        pytest.skip("steering-files/ submodule not initialised")
    with tempfile.TemporaryDirectory() as ws:
        place_rules(ws, str(rules), language="ko")
        text = (Path(ws) / "CLAUDE.md").read_text(encoding="utf-8")
    assert "한국어로 진행" in text


# ---- ② 공유 CLAUDE_CONFIG_DIR (이 결함의 주 원인) ----


@pytest.mark.parametrize("config_dir", ["discovery-config", "proto-config"])
def test_shared_config_dir_claude_md_is_language_neutral(config_dir):
    """**이 결함의 주 원인이었다.**

    두 디렉토리 모두 setting_sources의 "user" 레벨이라 전 프로젝트가 공유한다 —
    프로젝트별 언어를 담을 수 없다. 스펙 §3은 여기서 언어 *지시 한 줄*을
    지웠지만, 문서 전문이 한국어 산문으로 남아 있었다. 모델에게는 그 자체가
    언어 신호다: 영어 프로젝트의 에이전트가 매 턴 1935자의 한국어를 읽으면서
    워크스페이스의 "conduct all conversation in English"와 경쟁했다.

    지시 유무가 아니라 글자를 보는 이유가 이것이다. 이 파일을 한국어로 되돌리는
    수정은 이 테스트를 통과할 수 없다.

    **proto-config가 이 단정에 늦게 들어왔다(2026-08-13).** discovery만
    검사하던 동안 proto-config/CLAUDE.md는 5,283자 중 1,807자가 한글인 상태로
    남아 있었다 — 같은 구조, 같은 실패 경로인데 한쪽만 지키면 다른 쪽은 조용히
    되돌아간다. 빌드 에이전트의 프로젝트 언어는 proto/prompts.py(두 언어 완본)와
    워크스페이스 CLAUDE.md로 오고, 그 둘만이 프로젝트별로 달라질 수 있다.
    """
    path = REPO / config_dir / "CLAUDE.md"
    if not path.is_file():
        pytest.skip(f"{config_dir}/CLAUDE.md not present")
    text = path.read_text(encoding="utf-8")
    assert not hangul(text), (
        f"공유 config dir({config_dir})의 CLAUDE.md는 전 프로젝트가 읽으므로 "
        f"언어 중립이어야 한다. 한글 {len(hangul(text))}자 발견: {hangul(text)[:12]}")


# ---- ③ MCP 도구 설명·반환 문자열 (모델이 읽는 프롬프트) ----
#
# **대상이 Discovery에서 프로토타입 빌더로 옮겨 왔다(2026-08-21).** 여기 있던 검사들은
# `agent/tools.py`의 `build_tools`를 봤는데, 그 모듈의 유일한 도구
# (`submit_document`)가 PostToolUse 훅으로 대체되면서 모듈째 사라졌다. 남은 커스텀
# 도구는 빌더의 `build_complete` 하나이고, 그것도 같은 실패 경로를 갖는다 — 도구 설명은
# 모델이 매 턴 도구 목록과 함께 읽는 프롬프트다. 그래서 검사를 지우지 않고 옮긴다
# (report_stage 검사가 test_agent_reconcile.py로 옮겨 간 것과 같은 규율).


def _proto_tools(language: str | None = None):
    if language is None:
        return build_proto_tools("/tmp/ws", lambda e: None)
    return build_proto_tools("/tmp/ws", lambda e: None, language)


def test_english_tool_descriptions_have_no_korean():
    """도구 설명은 매 턴 모델 컨텍스트에 들어가는 프롬프트다."""
    bad = {t.name: t.description for t in _proto_tools("en")
           if hangul(t.description)}
    assert not bad, f"영어 프로젝트의 도구 설명에 한글이 있다: {bad}"


def test_korean_tool_descriptions_are_still_korean():
    """대칭 확인 — 영어를 배선하면서 한국어를 잃지 않는다."""
    descs = [t.description for t in _proto_tools("ko")]
    # 비어 있으면 all()이 참이 되어 이 검사가 조용히 무의미해진다.
    assert descs, "커스텀 도구가 하나도 없다 — 이 검사가 공허해졌다"
    assert all(hangul(d) for d in descs), descs


def test_build_proto_tools_defaults_to_korean():
    """인자를 안 주는 호출부(구 코드, 테스트)가 기존 동작을 유지한다."""
    descs = [t.description for t in _proto_tools()]
    assert descs, "커스텀 도구가 하나도 없다 — 이 검사가 공허해졌다"
    assert all(hangul(d) for d in descs), descs


async def test_build_complete_refusals_follow_the_project_language(tmp_path):
    """거부 문자열은 에이전트가 읽고 스스로 고치는 지시다 — 대화 언어와 같아야 한다.

    `test_submit_document_refusals_follow_the_project_language`가 여기 있었다. 그
    도구가 사라졌으므로 같은 계약을 가진 도구에서 같은 것을 본다: 산출물 없이 완료를
    선언하면 사용자는 "빌드 완료" 카드를 보는데 호스팅할 것이 없다.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    tools = {t.name: t for t in build_proto_tools(str(ws), lambda e: None, "en")}
    out = (await tools["build_complete"].handler(
        {"summary": "done"}))["content"][0]["text"]
    assert not hangul(out), out


# ---- ④ 드라이버가 만드는 모델·사용자 대상 텍스트 ----


@pytest.mark.parametrize("fn,kwargs", [
    (prompts.answer_first, {}),
    (prompts.turn_failed, {}),
    (prompts.question_payload_rejected, {"reason": "no options"}),
    (prompts.state_file_missing, {}),
    (prompts.prototype_handoff_stop, {"slug": "prototype"}),
    (proto_prompts.design_rules, {}),
    (proto_prompts.build_complete_theme_rejection, {}),
    (proto_prompts.unsafe_command_refused, {"fragment": "npm run dev"}),
])
def test_every_english_prompt_has_no_korean(fn, kwargs):
    """prompts.py의 모든 영어 갈래를 전수로 훑는다.

    새 함수를 추가하면서 en 갈래를 빼먹는 것이 이 결함의 재발 경로이므로,
    개별 호출부가 아니라 이 모듈 자체를 검사한다.
    """
    out = fn("en", **kwargs)
    assert not hangul(out), f"{fn.__name__}('en')에 한글: {out}"
    assert out.strip(), f"{fn.__name__}('en')이 비었다"


@pytest.mark.parametrize("fn,kwargs", [
    (prompts.answer_first, {}),
    (prompts.turn_failed, {}),
    (prompts.question_payload_rejected, {"reason": "옵션 없음"}),
    (prompts.state_file_missing, {}),
    (prompts.prototype_handoff_stop, {"slug": "prototype"}),
    (proto_prompts.design_rules, {}),
    (proto_prompts.build_complete_theme_rejection, {}),
    (proto_prompts.unsafe_command_refused, {"fragment": "npm run dev"}),
])
def test_every_korean_prompt_is_korean_and_unknown_falls_back(fn, kwargs):
    """ko 갈래가 살아 있고, 손상된 매니페스트의 임의 문자열이 ko로 떨어진다
    (place_rules와 같은 규율 — 프롬프트 없이 도는 것보다 낫다)."""
    assert hangul(fn("ko", **kwargs)), fn.__name__
    assert fn("klingon", **kwargs) == fn("ko", **kwargs), fn.__name__


def test_answers_resumed_carries_the_record_in_both_languages():
    """재시작 후 답변 전달 프롬프트. `record`는 어느 질문 라운드의 답변인지의
    유일한 영속 흔적이므로(S3 pending은 나가는 길에 삭제된다) 두 언어 모두
    그것을 담아야 한다."""
    record = '{"interrupt_id": "i-1"}'
    lines = "- Q → A"
    en = prompts.answers_resumed("en", lines, record)
    ko = prompts.answers_resumed("ko", lines, record)
    for out in (en, ko):
        assert record in out and lines in out
    assert not hangul(en), en
    assert hangul(ko), ko


# `test_submit_document_description_keeps_the_write_first_order_in_both`이 여기
# 있었다. 그 문장의 핵심은 순서 지시("먼저 쓴 뒤 호출")였고, 도구가 사라지면서 지킬
# 순서 자체가 없어졌다 — 이제 신호가 쓰기에서 유도되므로 순서가 뒤집힐 수 없다.


# ---- ⑤ 드라이버 배선 ----
#
# `test_driver_passes_its_language_to_the_tools`가 여기 있었다. Discovery의 커스텀
# 도구(`submit_document`)가 2026-08-21에 사라지면서 검사할 대상이 없어졌다 — Discovery는
# 커스텀 도구를 하나도 배선하지 않으므로 그 채널로 언어가 샐 수 없다
# (claude_driver의 `mcp_servers` 주석). 남은 도구는 빌더의 것이고 위 ③이 본다.
