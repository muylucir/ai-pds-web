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
        # 두 문구를 지우는 것만으로는 부족하다: **문서의 언어 자체가 언어
        # 신호다**(이 파일들의 상단 WHY 주석, 2026-08-04 실측 — 지시 한 줄을
        # 지웠는데도 영어 프로젝트의 대화가 계속 한국어로 돌았다). 그래서
        # 글자를 센다. 이 검사는 둔하지만 그 점이 장점이다 — 여기에 한국어로
        # 조항을 추가하려는 어떤 시도도 걸린다.
        assert not {c for c in text if "가" <= c <= "힣"}, (
            f"{rel} 은 언어 중립이어야 한다 — 한글이 있으면 그 자체가 언어 신호다")


#: 작성 깊이 기준(공유 config)과 언어 조항(언어 지시)을 각각 가리키는 앵커.
#: 두 언어 파일이 단어를 공유하지 않으므로(한쪽은 한국어 산문) 공유할 수 있는
#: 것은 ASCII 마커뿐이다 — test_proto_prompts가 "AskUserQuestion" 같은 공유
#: 토큰으로 두 벌의 대칭을 검사하는 것과 같은 방법이다.
_DEPTH_BAR_MARKER = "<!-- depth-bar-items: derive, prose, unknowns, brackets, defaults -->"
_LANGUAGE_CLAUSE_MARKER = "<!-- depth-bar-language-clause -->"


def test_the_depth_bar_lives_in_the_shared_config_not_the_language_directives():
    """**작성 깊이 기준은 공유 config에 한 벌로 둔다.**

    깊이는 언어 중립이다 — 어느 언어로 쓰든 같은 기준이 적용된다. 언어 지시에
    두면 한국어 한 벌과 영어 한 벌이 되고, 두 벌은 갈라진다: 한쪽에만 조항이
    추가되는 회귀는 에러 없이 산출물 품질 차이로만 나타난다.

    자리를 이렇게 정한 근거는 리포의 선례다. 상류 룰(aws-aiplc-rule-details)이
    부족할 때 그것을 고치지 않고 discovery-config에서 override를 선언한다 —
    질문 파일 규약과 프로토타입 빌드 금지가 그 두 선례이고, 이것이 세 번째다.
    상류 룰을 고치면 재동기화 때 조용히 사라진다(e12d806 → 2047ac3이 언어 지시로
    같은 실패를 한 번 겪었다).

    2026-08-13 실측이 이 기준이 필요한 이유다: 같은 입력을 언어만 바꿔 넣은 두
    세션이 산문 비중 20% vs 58%(484자 vs 3,823자)로 갈렸고 **둘 다 필수 영역
    완전성 검사는 통과했다.** 완전성은 기준이 아니다.
    """
    repo = Path(__file__).resolve().parents[2]
    config = repo / "discovery-config" / "CLAUDE.md"
    if not config.is_file():
        pytest.skip("discovery-config/CLAUDE.md not present")
    text = config.read_text(encoding="utf-8")
    assert _DEPTH_BAR_MARKER in text
    assert "overrides the upstream rules" in text
    # 마커만 남고 항목이 사라지는 것을 막는다 — 마커는 목록의 목차이고, 목차와
    # 본문이 어긋나면 검사가 의미를 잃는다.
    body = text.split(_DEPTH_BAR_MARKER, 1)[1]
    assert body.count("\n- **") >= 5

    # 두 벌이 되지 않아야 한다. 언어 지시에도 같은 기준이 들어가면 드리프트가
    # 시작되고, 어느 쪽이 최신인지 알 수 없다.
    rules = repo / "rule" / "aiplc-rules"
    if not (rules / "language" / "ko.md").is_file():
        return
    for language in ("ko", "en"):
        directive = (rules / "language" / f"{language}.md").read_text(
            encoding="utf-8")
        assert _DEPTH_BAR_MARKER not in directive, language


def test_both_language_directives_carry_the_length_calibration_clause():
    """언어에 걸린 절반은 언어 지시에 남는다.

    모델은 분량을 **토큰**으로 자기조절하고 토큰 비용은 언어마다 3배 다르다 —
    "적당한 길이"라는 감각이 언어별로 다른 결과를 준다는 사실은 언어 규약의
    일부다. 이 조항이 사라지면 깊이 기준(공유 config)이 왜 필요한지에 대한
    설명이 어느 문서에도 남지 않는다.
    """
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    if not (repo_rules / "language" / "ko.md").is_file():
        pytest.skip("repo rules not present")
    for language in ("ko", "en"):
        text = (repo_rules / "language" / f"{language}.md").read_text(
            encoding="utf-8")
        assert _LANGUAGE_CLAUSE_MARKER in text, language
        # 깊이 기준이 어디 있는지 가리켜야 한다 — 가리키는 문장이 없으면 그
        # 기준은 이 문서를 먼저 읽는 에이전트에게 존재하지 않는 것과 같다.
        assert "Depth of what you write" in text, language


def test_upstream_question_rules_are_untouched():
    """상류 룰은 고치지 않는다 — 질문 파일 규약도 예외가 아니다.

    Pathfinder는 `[Answer]:` 칸을 백엔드가 채우고(agent/question_file_answers.py)
    사용자는 그 파일을 UI에서 편집할 수 없다. 그래서 question-format-guide.md의
    "Missing Answers"(사용자를 그 파일로 보내는 처리)와 Step 3(사용자가 "done"이라고
    말할 때까지 대기)은 이 제품에 맞지 않는다 — 그렇다고 그 파일을 지우거나 고치는
    것은 금지다. 상류 룰은 데이터이고, 갱신하면 로컬 수정이 조용히 사라진다.
    대신 discovery-config가 어느 쪽이 이기는지 선언한다(아래 테스트).
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
    """답변 되기록의 규약이 discovery-config에 적혀 있어야 한다.

    없으면 두 규정이 한 상황에 적용되고 어느 쪽이 이길지 예측할 수 없다 —
    상류 question-format-guide는 빈 `[Answer]:`를 보면 사용자에게 그 파일에 답을
    적으라고 하고(UI에서 편집할 수 없다), "done"이라고 말할 때까지 기다리라고
    한다(AskUserQuestion 왕복이 이미 확인이다). 그 충돌이 7f33652의 언어 지시
    이중화와 같은 실패 모양이므로, 프로토타입 섹션과 같은 방식으로 어느 쪽이
    이기는지 문서에 적어 둔다.

    **에이전트가 알아야 하는 두 가지를 특히 검사한다.** 되기록은 백엔드가
    하므로 (1) 에이전트가 그 칸을 직접 쓰면 두 writer가 한 줄을 다투고,
    (2) 매칭이 질문 텍스트로 이뤄지므로 파일과 도구의 문장이 갈리면 답이
    조용히 심기지 않는다 — 둘 다 에러 없이 실패하는 모양이라 문서에 없으면
    아무도 모른다.
    """
    repo = Path(__file__).resolve().parents[2]
    path = repo / "discovery-config" / "CLAUDE.md"
    if not path.is_file():
        pytest.skip("discovery-config/CLAUDE.md not present")
    text = path.read_text(encoding="utf-8")
    # override라고 명시적으로 선언한다(프로토타입 섹션의 선례와 같은 표현).
    assert "overrides the upstream rules" in text
    assert "[Answer]:" in text
    # audit.md는 정본에서 감사 추적으로 역할이 바뀌었을 뿐, 계속 요구된다.
    assert "audit.md" in text
    # 되기록의 주체가 백엔드라는 것.
    assert "Do not write them yourself" in text
    # 매칭 키가 번호가 아니라 텍스트라는 것.
    assert "by question text, not by question number" in text


def _discovery_config() -> str:
    """공백을 접은 discovery-config/CLAUDE.md.

    줄바꿈을 접는 이유: 이 절의 문장은 80칼럼으로 감겨 있어서 원문 부분문자열
    검사는 **줄바꿈 위치**를 검사하게 된다. 그러면 문단을 다시 감기만 해도
    테스트가 깨지고(내용은 그대로인데), 반대로 검사를 통과시키려 문장을 한 줄로
    늘어놓는 압력이 생긴다. 여기서 고정하려는 것은 규칙의 내용이다.
    """
    path = (Path(__file__).resolve().parents[2] / "discovery-config" / "CLAUDE.md")
    if not path.is_file():
        pytest.skip("discovery-config/CLAUDE.md not present")
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_prototype_scope_rule_is_a_boundary_not_a_command_list():
    """**2026-08-16의 결함이 이 검사의 이유다.**

    에이전트가 워크스페이스에 `prototype/index.html`을 만들었다. 당시 이 절이
    금지한 것은 빌드 *명령*이었다 — npm install / npm run dev / 서브프로세스 /
    포트 선택. 자기완결 HTML 한 장은 그중 아무것도 필요하지 않고, 에이전트의 자기
    보고("패키지 설치·외부 통신 모두 불필요")가 곧 모든 조항을 만족했다는 증거다.

    그래서 규칙이 **경계**(aiplc-docs/ 밖 금지)로 서술돼야 한다. 명령 열거로
    되돌아가면 다음 우회는 다른 모양으로 온다 — 열거는 빠진 항목을 초대한다.
    강제 장치가 있다는 사실도 적혀 있어야 한다: 모르면 거부를 버그로 오해하고
    경로만 바꿔 재시도한다(agent/discovery_guard.py 헤더 참조).
    """
    text = _discovery_config()
    assert "You write only under `aiplc-docs/`" in text
    # 강제된다는 사실 + 거부가 무엇을 알려주는지.
    assert "enforced, not trusted" in text


def test_the_slugged_spec_is_required_on_every_path_including_a1():
    """상류 Path A.1(prototype-validation.md)은 `prototype/prototype-spec.md`만
    만들고 끝낸다. 그런데 Prototypes 탭이 카드를 만드는 유일한 경로는
    `prototypes/{slug}/PROTOTYPE-{slug}.md`이고(routes/prototypes.py의 _SPEC_RE),
    그 정규식은 디렉터리 캡처를 파일명에서 백레퍼런스한다 — 슬러그가 어긋나면
    카드가 아예 없고, 빌드·호스팅·설문·삭제가 모두 그 슬러그로 키된다.

    그러므로 "Path A.1도 예외가 아니다"가 문서에 있어야 한다. 없으면 상류 문서를
    그대로 따른 세션이 공유용 산출물 없이 끝나고, 그 실패는 Prototypes 탭이
    비어 있는 것으로만 나타난다.
    """
    text = _discovery_config()
    assert "on every path — including Path A.1" in text
    assert "PROTOTYPE-{slug}.md" in text
    # 슬러그 == 디렉터리명이라는 제약과 새니타이즈 규칙.
    assert "must match the directory name exactly" in text
    assert "lowercase letters, digits and hyphens only" in text
    # 정본이 어느 쪽인지 — 11KB 쌍둥이를 유지하면 드리프트가 시작된다.
    assert "artifact of record" in text


#: 조립된 워크스페이스 CLAUDE.md의 인코딩 절을 가리키는 앵커.
_ENCODING_MARKER = "<!-- pathfinder-tool-encoding -->"


def test_the_assembled_claude_md_carries_the_tool_encoding_rule(tmp_path):
    """**2026-08-16 keumkang-v3의 결함이 이 검사의 이유다.**

    모델이 툴 파라미터의 한글을 `\\uXXXX` 이스케이프로 쓰면서 hex를 오타내면
    "유효하지만 틀린" 음절이 된다(anthropics/claude-code#83033, 공식 미해결 —
    모델 팀 이관, CLI로는 복원 불가). 실측: 파일은 `제공하시겠습니까`(U+ACA0)인데
    물어본 질문은 `제공하시겜습니까`(U+AC9C)였다. 사용자는 깨진 한국어를 보고,
    되기록은 짝을 못 찾는다.

    상류가 권고하는 유일한 완화책이 이 지시다. 그런데 `discovery-config/CLAUDE.md`
    에만 있었고, 그 파일은 스스로 "UI 접점에만 적용된다"며 모델을 작업 디렉터리
    CLAUDE.md로 보낸다 — **거기에는 조항이 없었다.** 그래서 모델이 실제로 지목받는
    파일에 넣는다.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")

    assert _ENCODING_MARKER in text
    # 지시의 두 축: 리터럴 UTF-8로 쓸 것, `\uXXXX`를 쓰지 말 것. 공백을 접고
    # 보는 이유는 이 절이 80칼럼으로 감겨 있어서다 — 원문 부분문자열 검사는
    # 내용이 아니라 줄바꿈 위치를 검사하게 된다(_discovery_config와 같은 규율).
    folded = " ".join(text.split())
    assert "literal UTF-8" in folded
    assert "\\uXXXX" in folded
    # 언어 지시보다 앞에 온다 — 출력 형식은 문서 전체의 전제다.
    assert text.index(_ENCODING_MARKER) < text.index("KO-DIRECTIVE")


def test_the_encoding_rule_is_language_neutral(tmp_path):
    """인코딩 규칙은 어느 언어를 쓸지에 대해 아무 말도 하지 않는다.

    한글이 섞이면 그 자체가 언어 신호가 되어(이 파일 상단의 실측 근거) 영어
    프로젝트의 대화를 한국어로 끌어당긴다 — 7f33652의 실패 모양이다.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="en")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")

    block = text.split(_ENCODING_MARKER, 1)[1].split("EN-DIRECTIVE", 1)[0]
    assert not {c for c in block if "가" <= c <= "힣"}, block


def test_the_config_dir_does_not_scope_the_encoding_rule_away(tmp_path):
    """`discovery-config/CLAUDE.md`가 "이 파일은 UI 접점에만 적용된다"고 말하면서
    인코딩 조항을 같은 파일에 두면, 모델이 그 조항도 UI 접점 한정으로 읽을 수 있다.
    그 축소가 실제 결함의 절반이었으므로 문서가 명시적으로 부인해야 한다."""
    text = _discovery_config()
    assert "literal" in text and "uXXXX" in text
    # 인코딩 규칙만은 범위 축소의 예외라고 못박아야 한다.
    assert "applies to every tool call" in text
