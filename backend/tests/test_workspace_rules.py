# 상류(aws-samples/sample-ai-plc) 레이아웃을 워크스페이스에 재현한다:
#   core-workflow.md → CLAUDE.md,  aws-aiplc-rule-details/ → 그 이름 그대로.
# core-workflow.md:18이 `Rule details location: ./aws-aiplc-rule-details/`로
# CWD 상대경로를 전제하므로, 룰이 워크스페이스에 있어야 에이전트가 그 경로를
# 그대로 읽는다.
from pathlib import Path

import pytest

from aipds.agent.workspace_rules import place_rules


#: 실제 언어 지시의 첫 헤딩. 픽스처 스텁(옛 `KO-DIRECTIVE`/`EN-DIRECTIVE`)을 쓰지
#: 않는 이유: 지시가 룰셋 트리 밖(이제 코드 상수)으로 옮겨졌고,
#: 픽스처가 그것을 흉내내려면 모듈 속성을 monkeypatch해야 한다. 실물을
#: 그대로 쓰면 픽스처와 실물의 드리프트도 함께 사라진다 — 그 드리프트가
#: `test_works_against_the_real_repo_rules`가 존재하는 이유다.
_KO_MARK = "# 언어 규약"
_EN_MARK = "# Language convention"


def _directive(language: str) -> str:
    """언어 지시는 **코드**다(`workspace_rules.LANGUAGE_DIRECTIVES`).

    2026-08-19에 `aipds/agent/language/{ko,en}.md`에서 옮겼다. 파일이었기
    때문에만 존재했던 실패 경로가 사라진다 — 문자열 리터럴은 잃어버릴 수 없으므로
    "지시가 없는 채로 조립한다"가 구조적으로 불가능해진다(옛
    `test_raises_when_the_language_directive_is_missing`은 그 상태를 만들려고
    탐색 경로를 monkeypatch해야 했고, docstring이 스스로 "설치가 망가진 경우에만
    실현된다"고 적어 뒀다).

    그리고 두 판이 한 파일에 나란히 놓인다. 파일 두 개로 떨어져 있던 동안 갈라져
    있었다 — ko 3,389자 / en 1,310자였고, en은 "번역할 것이 없다"로 끝내
    양식 처리 판단을 아예 담지 않았다.
    """
    from aipds.agent.workspace_rules import LANGUAGE_DIRECTIVES
    return LANGUAGE_DIRECTIVES[language]


def _rules(tmp_path: Path) -> Path:
    """리포의 steering-files/aiplc-rules 레이아웃을 흉내낸 픽스처.

    **언어 지시는 여기 없다.** 업스트림 `aiplc-rules/`에는 `.gitkeep`과
    `aws-aiplc-rules/`·`aws-aiplc-rule-details/`뿐이고(실측), 우리 지시는
    `agent/workspace_rules.LANGUAGE_DIRECTIVES`에 산다. 픽스처가 그 사실을
    반영해야 "룰셋을 통째로 갈아 끼운다"는 조작이 테스트에서도 안전하게 보인다.
    """
    rules = tmp_path / "rules"
    (rules / "aws-aiplc-rules").mkdir(parents=True)
    (rules / "aws-aiplc-rules" / "core-workflow.md").write_text(
        "# DISCOVERY PHASE WORKFLOW", encoding="utf-8")
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
    assert text.index(_KO_MARK) < text.index("# DISCOVERY PHASE WORKFLOW")
    assert _EN_MARK not in text


def test_english_project_gets_the_english_directive(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="en")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert _EN_MARK in text
    # 한국어 지시가 남으면 두 지시가 충돌한다 — 이것이 7f33652의 실패 모양이다.
    assert _KO_MARK not in text


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
    assert _KO_MARK in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_an_unknown_language_falls_back_to_korean(tmp_path):
    # 손상된 매니페스트가 임의 문자열을 실어 와도 룰 없이 돌지 않는다.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="klingon")
    assert _KO_MARK in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_switching_language_rewrites_claude_md(tmp_path):
    # 조립 결과는 원본 파일이 아니므로 크기 비교 최적화를 적용하지 않는다.
    # 두 언어 지시의 크기가 우연히 같아도 반드시 다시 써야 한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    place_rules(str(ws), str(rules), language="en")
    assert _EN_MARK in (ws / "CLAUDE.md").read_text(encoding="utf-8")


def test_the_language_directives_live_outside_the_upstream_ruleset():
    """**2026-08-18에 옮긴 자리를 고정한다.**

    업스트림 `aiplc-rules/`에는 `.gitkeep`·`aws-aiplc-rules/`·
    `aws-aiplc-rule-details/`뿐이고 `language/`는 없다(GitHub API로 확인).
    우리 지시를 그 트리 안에 두면 룰셋 교체가 "디렉터리를 통째로 갈아 끼운다"로
    끝나지 못하고 — 그렇게 하면 지시가 함께 사라진다 — 이 제품의 최우선 제약이
    사람의 주의력에 걸린다. 그리고 그 디렉터리는 읽기 전용으로 다루므로, 지시를
    고쳐야 할 때 손댈 수 없는 자리이기도 했다.

    되돌아가는 것을 막는 것이 이 검사다: 룰셋 트리에 `language/`가 다시 생기면
    실패한다. 이 불변식은 2026-08-21에 상류 리포를 `steering-files/` 서브모듈로
    참조하게 된 이행의 선행 조건이었다 — 그 트리는 이제 상류 소유이고, 우리
    콘텐츠를 둘 수 있는 자리가 아니다.
    """
    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "steering-files" / "aiplc-rules" / "language").exists()


def test_the_language_directive_is_code_not_a_file():
    """2026-08-19: 파일 두 개를 상수 두 개로 바꿨다.

    프로덕션 독자가 `place_rules` 하나뿐이었고, 파일이라는 사실이 사 온 것은
    "없을 수 있다"는 상태와 그것을 지키는 raise 하나였다. 상수는 그 상태를
    가질 수 없다.

    그리고 모델이 읽는 텍스트를 언어별 두 벌로 코드가 소유하는 것이 이 리포의
    기존 규약이다 — `agent/prompts.py`, `proto/prompts.py`, `survey/builder.py`,
    `survey/report_labels.py`가 모두 그 형태이고 `discovery_guard.py` 헤더가
    그것을 규약으로 적어 뒀다. `language/*.md`만 예외였다.
    """
    from aipds.agent.workspace_rules import LANGUAGE_DIRECTIVES
    assert set(LANGUAGE_DIRECTIVES) == {"ko", "en"}
    assert all(v.strip() for v in LANGUAGE_DIRECTIVES.values())

    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "backend" / "aipds" / "agent" / "language").exists(), (
        "언어 지시가 다시 파일이 됐다 — 상수여야 '없는 상태'가 불가능하다")


def test_both_directives_reconcile_the_template_critical():
    """양식의 CRITICAL을 **양쪽 판이 모두** 화해시켜야 한다.

    실패 메커니즘(7f33652)은 "양식 바로 앞의 `**CRITICAL**: ... exactly as
    defined ... Do NOT deviate`가 더 가까워서 먼 언어 지시를 이겼다"였다. 그
    CRITICAL을 이름으로 부르지 않으면 에이전트가 `envision.md`를 읽는 순간 우리
    규칙과 연결할 고리가 없다.

    **en 판에도 필요하다.** 옛 en.md는 "There is nothing to translate"로 끝내
    이 판단을 아예 담지 않았다 — 영어 프로젝트에서도 "구조는 유지하고 사용자
    노출 문구는 이 언어로"라는 결정은 필요하다(양식에 다른 언어의 리터럴이
    섞여 들어올 수 있고, 구조 마커와 번역 대상의 구분은 언어와 무관하다).
    """
    for language in ("ko", "en"):
        text = _directive(language)
        assert "CRITICAL" in text, f"{language}: 양식의 CRITICAL을 지목하지 않는다"
        needle = "구조" if language == "ko" else "structure"
        assert needle in text, f"{language}: 구조와 언어를 구분하지 않는다"


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


def test_skips_a_file_that_is_unchanged(tmp_path):
    """매 턴 수십 개 파일을 다시 쓰지 않는다 — 손대지 않았으면 건너뛴다.

    `copy2`가 mtime을 보존하므로 첫 배치 뒤 dst와 src의 (크기, mtime)이 같고,
    두 번째 호출은 아무것도 하지 않는다. `copyfile`로 되돌리면 mtime이 매번
    갱신돼 이 캐시가 사실상 꺼진다 — 그것을 이 검사가 잡는다.

    대상이 CLAUDE.md가 아니라 상세 룰인 것에 주의: CLAUDE.md는 조립물이라 비교
    없이 매번 쓴다(두 언어 지시가 우연히 같은 지문을 가지면 언어를 바꿔도 파일이
    그대로 남는다 — 정확히 이 스펙이 없애려는 침묵이다).
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    target = ws / "aws-aiplc-rule-details" / "common" / "process-overview.md"
    before = target.stat().st_mtime_ns
    place_rules(str(ws), str(rules), language="ko")
    assert target.stat().st_mtime_ns == before


def test_refreshes_a_rule_whose_size_did_not_change(tmp_path):
    """**크기만 비교하면 룰셋 교체가 조용히 반쯤 적용된다.**

    매 턴 배치하는 목적이 바로 룰셋 교체를 진행 중인 프로젝트에 닿게 하는
    것이다(룰은 S3에 없고 워크스페이스는 턴마다 재구성된다 —
    claude_driver._place_rules). 그런데 판정이 크기뿐이면 갱신된 상세 룰의 바이트
    수가 우연히 같을 때 그 파일만 낡은 채로 남는다. 에이전트가 옛 절차를 따르는
    것으로만 드러나므로 추적이 거의 불가능하다.

    `OVERVIEW` → `REVISED!!`가 아니라 **같은 길이**로 바꾸는 것이 요점이다.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules), language="ko")
    target = ws / "aws-aiplc-rule-details" / "common" / "process-overview.md"
    assert target.read_text(encoding="utf-8") == "OVERVIEW"

    src = rules / "aws-aiplc-rule-details" / "common" / "process-overview.md"
    src.write_text("REVISED!", encoding="utf-8")      # 길이 8, 원본과 동일
    assert src.stat().st_size == len("OVERVIEW")
    # mtime을 **명시적으로** 앞으로 민다. 커널의 coarse 시각은 tick 단위로만
    # 갱신되므로(실측 gap 20ms를 구분하는 파일시스템에서도) 위 배치와 이 쓰기가
    # 한 tick 안에 들어가면 src의 mtime이 그대로다 — 테스트 안에서만 가능한
    # 상황이고, 룰셋 교체는 배포 → 재시작 → 다음 턴이므로 항상 tick보다 멀다.
    # 여기서는 "배포가 파일을 갈아 끼웠다"를 결정적으로 재현한다.
    import os
    later = src.stat().st_mtime_ns + 1_000_000_000
    os.utime(src, ns=(later, later))

    place_rules(str(ws), str(rules), language="ko")
    assert target.read_text(encoding="utf-8") == "REVISED!"


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
    repo_rules = (Path(__file__).resolve().parents[2]
                  / "steering-files" / "aiplc-rules")
    if not (repo_rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        # steering-files/는 서브모듈이다 — 초기화하지 않았으면 비어 있다.
        pytest.skip("steering-files/ submodule not initialised")
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
    repo_rules = (Path(__file__).resolve().parents[2]
                  / "steering-files" / "aiplc-rules")
    core = repo_rules / "aws-aiplc-rules" / "core-workflow.md"
    if not core.is_file():
        # steering-files/는 서브모듈이다 — 초기화하지 않았으면 비어 있다.
        pytest.skip("steering-files/ submodule not initialised")
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
    for language in ("ko", "en"):
        assert _DEPTH_BAR_MARKER not in _directive(language), language


def test_both_language_directives_carry_the_length_calibration_clause():
    """언어에 걸린 절반은 언어 지시에 남는다.

    모델은 분량을 **토큰**으로 자기조절하고 토큰 비용은 언어마다 3배 다르다 —
    "적당한 길이"라는 감각이 언어별로 다른 결과를 준다는 사실은 언어 규약의
    일부다. 이 조항이 사라지면 깊이 기준(공유 config)이 왜 필요한지에 대한
    설명이 어느 문서에도 남지 않는다.
    """
    for language in ("ko", "en"):
        text = _directive(language)
        assert _LANGUAGE_CLAUSE_MARKER in text, language
        # 깊이 기준이 어디 있는지 가리켜야 한다 — 가리키는 문장이 없으면 그
        # 기준은 이 문서를 먼저 읽는 에이전트에게 존재하지 않는 것과 같다.
        assert "Depth of what you write" in text, language


def test_upstream_question_rules_are_untouched():
    """상류 룰은 고치지 않는다 — 질문 파일 규약도 예외가 아니다.

    AI-PDS는 `[Answer]:` 칸을 백엔드가 채우고(agent/question_file_answers.py)
    사용자는 그 파일을 UI에서 편집할 수 없다. 그래서 question-format-guide.md의
    "Missing Answers"(사용자를 그 파일로 보내는 처리)와 Step 3(사용자가 "done"이라고
    말할 때까지 대기)은 이 제품에 맞지 않는다 — 그렇다고 그 파일을 지우거나 고치는
    것은 금지다. 상류 룰은 데이터이고, 갱신하면 로컬 수정이 조용히 사라진다.
    대신 discovery-config가 어느 쪽이 이기는지 선언한다(아래 테스트).
    """
    repo_rules = (Path(__file__).resolve().parents[2]
                  / "steering-files" / "aiplc-rules")
    guide = (repo_rules / "aws-aiplc-rule-details" / "common"
             / "question-format-guide.md")
    if not guide.is_file():
        # steering-files/는 서브모듈이다 — 초기화하지 않았으면 비어 있다.
        pytest.skip("steering-files/ submodule not initialised")
    text = guide.read_text(encoding="utf-8")
    # 상류가 소유하는 두 지시. AI-PDS가 이것을 무력화하는 방법은 파일을
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
    (2) 매칭 키가 무엇인지 알아야 그것을 안정적으로 유지한다 — 둘 다 에러 없이
    실패하는 모양이라 문서에 없으면 아무도 모른다.

    2026-08-17에 (2)의 키가 **텍스트에서 번호로** 바뀌었다. 질문을 파일에서 그대로
    읽게 되면서 도구 호출과 문장을 맞출 이유가 없어졌고, 그 "동일 문장" 요구가
    질문 파일을 납작하게 만들던 원인이었다(19문항 중 15문항이 배경 산문 없는 한
    문단이었다). 번호는 파일 안에서만 유지하면 되므로 요구가 훨씬 약하다.
    """
    # 공백을 접어서 본다 — 줄바꿈 위치가 아니라 규칙의 내용을 고정한다
    # (_discovery_config의 docstring에 그 근거가 있다).
    text = _discovery_config()
    # override라고 명시적으로 선언한다(프로토타입 섹션의 선례와 같은 표현).
    assert "overrides the upstream rules" in text
    assert "[Answer]:" in text
    # audit.md는 정본에서 감사 추적으로 역할이 바뀌었을 뿐, 계속 요구된다.
    assert "audit.md" in text
    # 되기록의 주체가 백엔드라는 것.
    assert "Do not write them yourself" in text
    # 매칭 키가 번호라는 것 — 그리고 그 도구를 쓰지 않는다는 것.
    assert "matched by question **number**" in text
    assert "AskUserQuestion is not available" in text


def test_the_turn_ending_writes_are_named_and_ordered():
    """**2026-08-18의 결함이 이 검사의 이유다(test123456).**

    PostToolUse 훅은 어떤 파일 쓰기에서 턴을 끝내고, 같은 메시지에 배치된 뒤 도구
    호출은 실행되지 않는다(claude_driver._on_post_tool_use). 그런데 이 파일은 그
    사실을 적지 않고 "파일을 다 쓰면 턴이 끝난다"까지만 말했다. 실측 결과:
    에이전트가 audit.md → 질문 파일을 먼저 쓰고 welcome 메시지·Workspace Detection
    보고·GATE 안내를 **그 뒤에** 두었고, 전부 화면에 도달하지 않았다. 같은 턴의
    `report_stage`도 사라져 aiplc-state.md가 만들어지지 않았다.

    Claude Code에서 같은 룰로 돌린 3회 전부 같은 순서였다 — 로컬에는 훅이 없어서
    벌을 받지 않았을 뿐이다. 즉 순서를 적지 않으면 상류 룰을 그대로 따르는 에이전트가
    이 경로에서 진다.

    상류 룰은 이미 이 순서를 요구한다: `core-workflow.md`의 "MANDATORY: Custom
    Welcome Message"와 Workspace Detection 6단계("Present completion message to
    user")가 질문하는 스테이지보다 앞에 있다. 그래서 이것은 override가 아니라
    **상류 순서를 이 경로에서 성립시키는 진술**이다.

    **턴을 끊는 파일이 둘이라는 것을 함께 고정한다.** 처음에는 질문 파일 하나였고,
    2026-08-18에 `build-instructions.md`가 합류했다(`handoff_prototype` 도구가 훅으로
    옮겨 갔다 — agent/reconcile.py). 목록이 한 자리에 있어야 다음에 셋이 될 때 같은
    함정을 다시 파지 않는다.

    그리고 반대쪽 조항이 이 순서를 막지 않아야 한다. 옛 문구는 "do not announce
    that you are about to ask"였는데, 그것이 "쓰기 전에 말하지 마라"로 읽혀
    "Keep the conversation visible" 절과 정면으로 부딪쳤다 — 실측한 턴이 welcome을
    건너뛰고 두 문장만 남긴 것이 그 충돌의 모양이다.
    """
    text = _discovery_config()
    # 턴을 끊는 파일 둘이 한 자리에 이름으로 적혀 있다.
    assert "Turn-ending writes" in text
    assert "build-instructions.md" in text
    assert "[Answer]:" in text
    # 뒤에 배치한 호출이 **버려진다**는 것. 없으면 순서는 취향으로 읽히고, 이
    # 실패는 에러 없이 온다.
    assert "discarded" in text
    # 무엇이 앞에 오는지 지목한다 — 이유만 주면 모델이 즉흥한다(prompts.py 헤더).
    #
    # "submit_document"가 이 목록에 있었다. 그 도구는 2026-08-21에 PostToolUse 훅으로
    # 옮겨 갔으므로(agent/reconcile.document_events) 지목할 호출이 아니라 **쓰기**다 —
    # 없는 도구를 부르라는 지시가 남으면 모델이 그것을 찾다가 턴을 낭비한다.
    for before in ("document write", "audit.md", "aiplc-state.md"):
        assert before in text
    # 상류 근거를 지목해 둔다. 없으면 다음 사람이 이 조항을 AI-PDS의 변덕으로
    # 읽고 상류 재동기화 때 지운다.
    assert "core-workflow.md" in text
    # 충돌 문구가 돌아오지 않는다. 질문을 **옮겨 적는 것**만 금지여야 한다.
    assert "do not announce that you are about to ask" not in text
    assert "Do not restate the questions in chat" in text


def test_the_state_file_is_the_agents_job_and_no_tool_is_named():
    """스테이지 갱신이 도구에서 파일로 돌아온 것을 고정한다.

    2026-08-18까지 이 파일은 "`report_stage`를 부르고 상태 파일은 직접 쓰지 마라"고
    상류를 override했다. 그 도구가 훅으로 대체되면서(agent/reconcile.py) override의
    근거가 사라졌다 — 상류 룰은 원래 에이전트가 이 파일을 직접 갱신하라고 요구하고
    (`common/workflow-changes.md`, 각 스테이지의 "Update State Tracking"),
    AI-PDS는 그것을 읽는다. 즉 이 경로는 로컬 Claude Code와 같아졌다.

    **없는 도구를 부르라고 적혀 있으면 안 된다.** 이름이 남아 있으면 에이전트가
    존재하지 않는 도구를 찾고, 그 실패는 조용하다(도구 목록에 없으므로 호출 자체가
    성립하지 않는다).
    """
    text = _discovery_config()
    assert "report_stage" not in text
    assert "handoff_prototype" not in text
    # 무엇을 해야 하는지가 그 자리를 채운다 — 금지만 남기면 모델이 즉흥한다.
    assert "aiplc-docs/aiplc-state.md" in text
    assert "Stage Progress" in text
    assert "workflow-changes.md" in text


def _discovery_config() -> str:
    """공백을 접은 discovery-config/CLAUDE.md.

    줄바꿈을 접는 이유: 이 절의 문장은 80칼럼으로 감겨 있어서 원문 부분문자열
    검사는 **줄바꿈 위치**를 검사하게 된다. 그러면 문단을 다시 감기만 해도
    테스트가 깨지고(내용은 그대로인데), 반대로 검사를 통과시키려 문장을 한 줄로
    늘어놓는 압력이 생긴다. 여기서 고정하려는 것은 규칙의 내용이다.
    """
    return _folded_config("discovery-config")


def _proto_config() -> str:
    """공백을 접은 proto-config/CLAUDE.md — 이유는 `_discovery_config`와 같다."""
    return _folded_config("proto-config")


def _folded_config(directory: str) -> str:
    path = Path(__file__).resolve().parents[2] / directory / "CLAUDE.md"
    if not path.is_file():
        pytest.skip(f"{directory}/CLAUDE.md not present")
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


def test_both_prototype_layouts_are_documented_as_valid():
    """**2026-08-16의 오판을 되돌린 자리다.**

    처음에는 "Path A.1이 슬러그 파일을 빠뜨렸다"고 보고 그것을 쓰라고 지시했다.
    틀렸다: `prototype-validation.md`가 선언하는 산출물 7개는 전부 단수
    `prototype/`이고(556-562행), 그 문서의 `PROTOTYPE-` 유일한 언급(16행)은 "이미
    있으면 빌드로 간다"는 진입 조건이다. 단일 프로토타입에는 구별할 대상이 없으니
    슬러그가 될 것도 없다.

    그래서 문서는 **두 레이아웃이 모두 정당하다**고 말해야 하고, 특히 슬러그
    사본을 만들지 말라고 해야 한다 — 8KB 명세의 두 사본은 드리프트하고 그 차이는
    에러 없이 내용 차이로만 나타난다. 카드 탐색이 두 레이아웃을 인식하는 쪽은
    proto/layout.py가 담당한다(tests/test_proto_layout.py).
    """
    text = _discovery_config()
    # 두 경로가 각각 어디에 쓰는지 적혀 있어야 한다.
    assert "prototype/prototype-spec.md" in text
    assert "prototypes/{slug}/PROTOTYPE-{slug}.md" in text
    # 사본 금지 — 이것이 되돌린 판단의 핵심이다.
    assert "Do not produce a slugged duplicate" in text
    # 트리 다이어그램의 "All paths"를 어떻게 읽어야 하는지 남긴다 — 그 문장이
    # 오판의 출발점이었으므로 다음 사람이 같은 길로 가지 않게 한다.
    assert "governs a path beats the overview diagram" in text
    # 슬러그를 쓰는 경로에서는 디렉터리명 == 파일명 제약이 여전히 유효하다.
    assert "slug must equal the directory name" in text
    assert "lowercase letters, digits and" in text


#: 조립된 워크스페이스 CLAUDE.md에 인코딩 절이 **없어야** 함을 확인하는 앵커.
_ENCODING_MARKER = "<!-- aipds-tool-encoding -->"


def test_the_assembled_claude_md_does_not_duplicate_the_encoding_rule(tmp_path):
    """**2026-08-18에 뒤집은 검사다.** 전에는 이 절이 있어야 한다고 요구했다.

    원래 근거(2026-08-16 keumkang-v3): 모델이 툴 파라미터의 한글을 `\\uXXXX`로
    쓰면서 hex를 오타내면 "유효하지만 틀린" 음절이 된다 — 파일은
    `제공하시겠습니까`(U+ACA0)인데 물어본 질문은 `제공하시겜습니까`(U+AC9C)였다.
    지시는 `discovery-config/CLAUDE.md`에 이미 있었는데도 결함이 났고, 그 파일이
    스스로 "UI 접점에만 적용"이라며 모델을 작업 디렉터리 CLAUDE.md로 보냈기
    때문이었다. 그래서 그쪽에도 복제했다.

    근거가 양쪽에서 사라졌다:

    1. **그 실패는 AskUserQuestion 경로의 것이었다.** 같은 턴에 Write로 쓴 파일은
       깨끗하고 그 도구의 입력만 깨졌다(agent/question_file_answers.py). 그 도구는
       이제 기본으로 거부된다(claude_driver.FILE_QUESTIONS_ENV).
    2. **공유 config가 스스로 좁히기를 그만뒀다** — "applies to every tool call
       ... nothing below narrows it"이 그 자리에 명시돼 있고, 아래
       `test_the_config_dir_does_not_scope_the_encoding_rule_away`가 고정한다.

    그래서 복제를 지운다. 한 규칙이 두 곳에 있으면 어느 쪽이 최신인지 알 수
    없다 — 이 파일이 깊이 기준에 대해 이미 같은 이유로 고정한 원칙이다
    (`test_the_depth_bar_lives_in_the_shared_config`).

    **규칙 자체가 사라지지 않았음을 함께 확인한다.** 복제 제거와 규칙 유실은
    코드에서 한 글자 차이이고, 후자는 깨진 한국어 질문으로만 드러난다.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")

    assert _ENCODING_MARKER not in text
    folded = " ".join(text.split())
    assert "literal UTF-8" not in folded

    # **규칙이 완전히 사라지지는 않았음을 함께 확인한다.** 복제 제거와 규칙 유실은
    # 코드에서 한 글자 차이이고, 후자는 깨진 한국어 질문으로만 드러난다. 남은 자리는
    # `proto-config`다 — 그쪽 경로가 AskUserQuestion을 여전히 필수로 쓴다
    # (test_the_encoding_rule_survives_only_where_askuserquestion_does에 전말).
    proto = _proto_config()
    assert "literal UTF-8" in proto
    assert "\\uXXXX" in proto


def test_the_assembled_claude_md_starts_with_the_language_directive(tmp_path):
    """언어 지시가 **1행**이다.

    인코딩 절을 떼어내며 비워진 자리를 이것이 받는다. 프로젝트마다 달라지는
    유일한 블록이고, 한 번 싸움에서 진 적이 있다 — 7f33652에서 "맥락이 가까운"
    템플릿의 CRITICAL이 언어 지시를 이겨 PR/FAQ 질문 20여 개가 영어로 남았다.
    문서 전체의 전제는 맨 앞에 둔다.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)), language="ko")
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")

    assert text.startswith(_KO_MARK)


def test_the_encoding_rule_survives_only_where_askuserquestion_does():
    """**비대칭이 의도다.** 2026-08-18에 두 config를 다르게 만들었다.

    인코딩 규칙(claude-code#83033: `\\uXXXX` hex 오타 → 다른 유효한 음절)은 한
    도구, AskUserQuestion의 완화책이었다. 두 경로의 사정이 반대다:

    - **Discovery**: 그 도구가 거부된다(claude_driver.FILE_QUESTIONS_ENV 기본 켜짐).
      질문은 파일로 하고, 깨진 한글이 와도 퍼지 매칭이 답변을 기록한다
      (agent/question_file_answers.py의 `_FUZZY_MIN`). 규칙이 할 일이 없다.
    - **프로토타입 빌드**: 그 도구가 **필수**다. proto/prompts.py가 "계획을 제시한
      뒤 반드시 AskUserQuestion으로" 승인을 받으라고 지시하고 proto/builder.py가
      거기서 대기한다. 질문 파일이 없으니 퍼지 매칭도 뒤에 없다.

    그래서 한쪽에서 지우고 한쪽에서 지킨다. "두 공유 config를 같은 모양으로"
    맞추려는 정리(132e409가 그 방향이었다)가 이 검사를 깨뜨릴 것이고, 그때
    읽어야 하는 것은 discovery-config/CLAUDE.md 상단의 제거 근거 주석이다.
    """
    proto = _proto_config()
    assert "literal" in proto and "uXXXX" in proto, (
        "프로토타입 경로는 AskUserQuestion이 필수다 — 규칙이 사라지면 승인 질문이 "
        "깨진 한글로 뜨고, 그쪽에는 퍼지 매칭이 없다")

    assert "as literal UTF-8" not in _discovery_config(), (
        "Discovery 경로에는 그 도구가 없다 — 규칙을 되살리면 죽은 완화책이 "
        "통합 계약의 맨 앞자리를 다시 차지한다")


def test_the_prototype_handoff_and_model_overrides_are_documented():
    """**2026-08-17의 두 결함을 한 절로 닫은 자리다.**

    Path A.1의 Step 3은 "Build Prototype"이고 상류 Step 4~11은 돌아가는
    프로토타입을 전제한다. AI-PDS는 빌드를 Prototypes 탭이 하는데, 금지만 있고
    멈출 지점·다음 행동이 문서에 없어서 에이전트가 즉흥 대응했다 — 실측
    keumkang-v5: 자격증명 점검 → API 키 요구 → 선행 조건 3건 나열, 탭 안내는 0회.

    같은 공백이 모델 문제로도 나타났다. `llm-model-configuration.md`가 제공자
    선택과 API 키를 요구하고 모델 ID 세 개를 서로 다르게 적어 두는데, 그것을
    무력화하는 절이 없었다. 프로젝트는 이미 모델을 갖고 빌드가 그것을 상속한다.

    **2026-08-18에 대체 행동이 도구에서 파일로 바뀌었다.** `handoff_prototype`이
    PostToolUse 훅으로 옮겨 갔으므로(agent/reconcile.py) 멈출 지점은 이제 도구 호출이
    아니라 `build-instructions.md` 쓰기다. 이 검사가 지키는 것은 그대로다: **멈출
    지점과 다음 행동이 문서에 있어야 한다.** 그것이 없을 때 실측된 결과가 위의
    즉흥 대응이고, 도구가 훅이 되어도 그 공백은 같은 모양으로 돌아온다.
    """
    text = _discovery_config()
    # 멈출 지점(무엇을 쓰면 끝나는가)과 그 뒤의 행동이 있어야 한다.
    assert "build-instructions.md" in text
    assert "that write is the handoff, and it ends your turn" in text.lower()
    # Step 4~6은 버리는 게 아니라 유보한다 — 그 말이 없으면 에이전트가 계속 간다.
    assert "not abandoned; they are deferred" in text
    # 모델·자격증명을 묻지 말라는 것, 그리고 스펙에 모델 ID를 쓰지 말라는 것.
    assert "already provisioned. Never ask for them" in text
    assert "do not write a model ID" in text


def test_the_config_says_where_a_document_preface_goes():
    """**2026-08-18 실측(123456test).** 에이전트가 "이 문서의 성격과 답변 방법"을
    `##` 섹션으로 써서, 1,100자 산문과 표가 통째로 **Question 1의 부연설명**이 되고
    그 헤딩이 6문항 전부의 카테고리로 붙었다.

    파서 결함이 아니다 — `## Question`이 아닌 `##`를 카테고리로 보고 그 아래 산문을
    다음 문항의 context로 돌리는 규칙 하나뿐이고(parsers/questions.py), 문서 머리말과
    문항별 부연설명은 그 규칙에게 같은 모양이다. 실측으로 확인한 갈림길: 머리말에
    `##`를 **붙이지 않으면** 그대로 `preamble`이 되고 Question 1은 깨끗하다.

    그러므로 고칠 수 있는 자리는 에이전트에게 자리를 알려주는 것뿐이다. 상류
    `question-format-guide.md`는 건드리지 않는다(재동기화 때 사라진다) — 이 파일의
    `overrides the upstream rules`가 그 자리다.

    "첫 문항 앞은 전부 preamble"로 파서를 바꾸지 않은 이유도 남긴다: 그 모양이
    **정당한** 실제 파일이 있다 — `pain-point-clarification-questions.md`의
    `## 확인 1 — 숫자가 맞지 않습니다`와 `design-context.md`의
    `## 제안한 프로토타입 범위 요약`은 바로 뒤 문항의 전제이고,
    `strategy-questions.md`의 `## Positioning`은 Q1~Q3를 묶는 진짜 카테고리다.
    """
    text = _discovery_config()
    assert "above the first `##` heading" in text
    # 잘못 두면 무엇이 일어나는지도 말해야 한다 — 이유 없는 지시는 지켜지지 않는다
    # (agent/prompts.py 헤더의 규율).
    assert "becomes the *first question's* context" in text
