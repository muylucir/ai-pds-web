# 상류(aws-samples/sample-ai-plc) 레이아웃을 워크스페이스에 재현한다:
#   core-workflow.md → CLAUDE.md,  aws-aiplc-rule-details/ → 그 이름 그대로.
# core-workflow.md:18이 `Rule details location: ./aws-aiplc-rule-details/`로
# CWD 상대경로를 전제하므로, 룰이 워크스페이스에 있어야 에이전트가 그 경로를
# 그대로 읽는다.
from pathlib import Path

import pytest

from pathfinder.agent.workspace_rules import place_rules


def _rules(tmp_path: Path) -> Path:
    """리포의 rule/aiplc-rules 레이아웃을 흉내낸 픽스처."""
    rules = tmp_path / "rules"
    (rules / "aws-aiplc-rules").mkdir(parents=True)
    (rules / "aws-aiplc-rules" / "core-workflow.md").write_text(
        "# DISCOVERY PHASE WORKFLOW", encoding="utf-8")
    lang = rules / "language"
    lang.mkdir(parents=True)
    (lang / "ko.md").write_text("KO-DIRECTIVE", encoding="utf-8")
    (lang / "en.md").write_text("EN-DIRECTIVE", encoding="utf-8")
    details = rules / "aws-aiplc-rule-details" / "common"
    details.mkdir(parents=True)
    (details / "process-overview.md").write_text("OVERVIEW", encoding="utf-8")
    return rules


def test_claude_md_is_language_directive_then_core_workflow(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    # 언어 지시가 **앞에** 온다. discovery-config/CLAUDE.md가 기록한 실패에서
    # "맥락이 가까운" 템플릿의 CRITICAL이 언어 지시를 이겼으므로, 여기서는
    # 언어를 문서 전체의 전제로 맨 앞에 둔다.
    assert text.index("KO-DIRECTIVE") < text.index("# DISCOVERY PHASE WORKFLOW")
    assert "EN-DIRECTIVE" not in text


def test_english_project_gets_the_english_directive(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="en")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert "EN-DIRECTIVE" in text
    # 한국어 지시가 남으면 두 지시가 충돌한다 — 이것이 7f33652의 실패 모양이다.
    assert "KO-DIRECTIVE" not in text


def test_the_two_languages_produce_different_claude_md(tmp_path):
    rules = _rules(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    place_rules(str(a), str(rules), language="ko")
    place_rules(str(b), str(rules), language="en")
    assert (a / "CLAUDE.md").read_text(encoding="utf-8") \
        != (b / "CLAUDE.md").read_text(encoding="utf-8")


def test_defaults_to_korean(tmp_path):
    # 인자를 안 주는 호출부(구 코드, 테스트)가 기존 동작을 유지한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)))
    assert "KO-DIRECTIVE" in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_an_unknown_language_falls_back_to_korean(tmp_path):
    # 손상된 매니페스트가 임의 문자열을 실어 와도 룰 없이 돌지 않는다.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="klingon")
    assert "KO-DIRECTIVE" in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_switching_language_rewrites_claude_md(tmp_path):
    # 조립 결과는 원본 파일이 아니므로 크기 비교 최적화를 적용하지 않는다.
    # 두 언어 지시의 크기가 우연히 같아도 반드시 다시 써야 한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    place_rules(str(ws), str(rules), language="en")
    assert "EN-DIRECTIVE" in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_raises_when_the_language_directive_is_missing(tmp_path):
    # core-workflow가 없을 때와 같은 규율이다: 룰 없이 조용히 진행하면
    # 에이전트가 언어를 모르는 채로 돌고, 그건 절반만 번역된 문서로 나타나
    # 원인 추적이 어렵다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    (rules / "language" / "ko.md").unlink()
    with pytest.raises(FileNotFoundError):
        place_rules(str(ws), str(rules), language="ko")


def test_copies_rule_details_under_the_name_the_rules_expect(tmp_path):
    # 이름이 바뀌면 `./aws-aiplc-rule-details/common/...` 읽기가 전부 깨진다.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)))
    assert (ws / "aws-aiplc-rule-details" / "common" / "process-overview.md") \
        .read_text(encoding="utf-8") == "OVERVIEW"


def test_is_idempotent(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    first = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    place_rules(str(ws), str(rules), language="ko")
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == first


def test_skips_a_file_already_present_with_the_same_size(tmp_path):
    # 매 턴 수십 개 파일을 다시 쓰지 않는다. 룰은 읽기 전용이므로 크기가 같으면
    # 같은 파일로 본다. mtime을 뒤로 밀어 두고 그대로인지 확인한다.
    #
    # 대상이 CLAUDE.md가 아니라 상세 룰인 것에 주의: CLAUDE.md는 이제 조립물이라
    # 크기 비교를 적용하지 않는다(두 언어 지시의 크기가 우연히 같으면 언어를
    # 바꿔도 파일이 그대로 남는다 — 정확히 이 스펙이 없애려는 침묵이다).
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    target = ws / "aws-aiplc-rule-details" / "common" / "process-overview.md"
    import os
    os.utime(target, (1, 1))
    place_rules(str(ws), str(rules), language="ko")
    assert target.stat().st_mtime == 1


def test_overwrites_a_file_whose_size_differs(tmp_path):
    # 상류 룰이 갱신되면 워크스페이스에도 반영돼야 한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    (ws / "CLAUDE.md").write_text("STALE", encoding="utf-8")
    place_rules(str(ws), str(rules), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# DISCOVERY PHASE WORKFLOW" in text and "STALE" not in text


def test_raises_when_core_workflow_is_missing(tmp_path):
    # 룰 없이 조용히 진행하면 에이전트가 워크플로우를 모르는 채로 돈다 —
    # 그건 빈 대화로 나타나서 원인 추적이 어렵다. 즉시 실패한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        place_rules(str(ws), str(empty))


def test_works_against_the_real_repo_rules():
    # 픽스처가 잘못된 레이아웃을 굳혀 실제 배치가 깨지는 것을 막는 통합 핀
    # (test_agent_tools.py의 test_file_read_reaches_real_rules_layout과 같은 이유).
    import tempfile
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    if not (repo_rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        pytest.skip("repo rules not present")
    for language in ("ko", "en"):
        with tempfile.TemporaryDirectory() as ws:
            place_rules(ws, str(repo_rules), language=language)
            assert (Path(ws) / "CLAUDE.md").is_file()
            assert (Path(ws) / "aws-aiplc-rule-details" / "common").is_dir()


def test_core_workflow_has_no_language_directive_of_its_own():
    """이 스펙의 핵심 불변식이다.

    상류 룰을 갱신하며 그 줄을 되살리면 조용히 충돌이 돌아온다 —
    영어 프로젝트에서 core-workflow의 '한국어로 진행'과 language/en.md가
    서로 반대를 말하고, 어느 쪽이 이길지 예측할 수 없다(7f33652).
    """
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    core = repo_rules / "aws-aiplc-rules" / "core-workflow.md"
    if not core.is_file():
        pytest.skip("repo rules not present")
    text = core.read_text(encoding="utf-8")
    assert "한국어로 진행" not in text


def test_shared_config_dirs_have_no_language_directive():
    """공유 CLAUDE_CONFIG_DIR은 전 프로젝트가 공유하므로 언어를 정할 수 없다.

    남겨두면 영어 프로젝트에서 워크스페이스의 language/en.md와 충돌한다.
    """
    repo = Path(__file__).resolve().parents[2]
    for rel in ("discovery-config/CLAUDE.md", "proto-config/CLAUDE.md"):
        path = repo / rel
        if not path.is_file():
            pytest.skip(f"{rel} not present")
        text = path.read_text(encoding="utf-8")
        assert "한국어로 진행" not in text, rel
        # 번역 오버라이드 절도 language/ko.md로 옮겨졌어야 한다.
        assert "번역해서 쓴다" not in text, rel


#: 두 언어 지시가 같은 깊이 기준을 담고 있는지 검사하기 위한 앵커. 두 파일이
#: 단어를 공유하지 않으므로(한쪽은 한국어 산문) 공유할 수 있는 것은 ASCII
#: 마커뿐이다 — test_proto_prompts가 "AskUserQuestion" 같은 공유 토큰으로
#: 두 벌의 대칭을 검사하는 것과 같은 방법이다.
_DEPTH_BAR_MARKER = ("<!-- depth-bar-items: brackets, paragraph-fields, "
                     "faq-answers, tables, defaults -->")


def test_both_language_directives_carry_the_same_depth_bar():
    """분량 기준은 **두 언어에 모두** 있어야 한다.

    한쪽에만 있으면 그 언어의 문서만 두꺼워지고, 그 비대칭은 에러 없이 산출물
    품질 차이로만 나타난다 — 2026-08-13 실측에서 섹션 수와 질문 수는 같은데
    필드별 밀도가 갈렸던 것이 정확히 그 모양이다.

    왜 기준이 필요한가: 모델은 분량을 **토큰**으로 자기조절하고 토큰 비용은
    언어마다 다르다. 스펙 2026-08-03-bilingual-ko-en은 "어느 언어로 쓰는가"만
    다뤘고 "얼마나 깊이 쓰는가"는 범위에 없었다. 대칭인 언어 지시가 대칭인
    결과를 주지 않는 이유가 그것이다.
    """
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    if not (repo_rules / "language" / "ko.md").is_file():
        pytest.skip("repo rules not present")
    for language in ("ko", "en"):
        text = (repo_rules / "language" / f"{language}.md").read_text(
            encoding="utf-8")
        assert _DEPTH_BAR_MARKER in text, language
        # 마커만 남고 항목이 사라지는 것을 막는다 — 마커는 목록의 목차이고,
        # 목차와 본문이 어긋나면 검사가 의미를 잃는다.
        body = text.split(_DEPTH_BAR_MARKER, 1)[1]
        assert body.count("\n- **") >= 5, language


def test_upstream_question_rules_are_untouched():
    """상류 룰은 고치지 않는다 — 질문 파일 규약도 예외가 아니다.

    Pathfinder는 질문을 AskUserQuestion으로 전달하므로 `[Answer]:` 칸이 영구히
    비어 있는데, 그렇다고 question-format-guide.md의 "Missing Answers" 처리를
    지우는 것은 금지다. 상류 룰은 데이터이고, 갱신하면 로컬 수정이 조용히
    사라진다. 대신 discovery-config가 override를 선언한다(아래 테스트).
    """
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    guide = (repo_rules / "aws-aiplc-rule-details" / "common"
             / "question-format-guide.md")
    if not guide.is_file():
        pytest.skip("repo rules not present")
    text = guide.read_text(encoding="utf-8")
    # 상류가 소유하는 두 지시. Pathfinder가 이것을 무력화하는 방법은 파일을
    # 고치는 것이 아니라 discovery-config에서 override를 선언하는 것이다.
    assert "If any [Answer]: tag is empty:" in text
    assert "#### Step 3: Wait for Confirmation" in text


def test_discovery_config_overrides_the_upstream_question_file_rules():
    """질문 파일을 기록물로 강등하는 선언이 discovery-config에 있어야 한다.

    없으면 두 규정이 한 상황에 적용되고 어느 쪽이 이길지 예측할 수 없다 —
    상류 question-format-guide는 빈 `[Answer]:`를 보면 사용자에게 다시 답을
    요구하라고 하고(그 파일은 UI에서 편집할 수 없다), "done"이라고 말할 때까지
    기다리라고 한다(AskUserQuestion 왕복이 이미 확인이다). 그 충돌이 7f33652의
    언어 지시 이중화와 같은 실패 모양이므로, 프로토타입 섹션과 같은 방식으로
    어느 쪽이 이기는지 문서에 적어 둔다.
    """
    repo = Path(__file__).resolve().parents[2]
    path = repo / "discovery-config" / "CLAUDE.md"
    if not path.is_file():
        pytest.skip("discovery-config/CLAUDE.md not present")
    text = path.read_text(encoding="utf-8")
    # override라고 명시적으로 선언한다(프로토타입 섹션의 선례와 같은 표현).
    assert "overrides the upstream rules" in text
    # 빈 [Answer]:가 정상 상태라는 것, 그리고 답변의 정본이 audit.md라는 것.
    assert "[Answer]:" in text
    assert "audit.md" in text
