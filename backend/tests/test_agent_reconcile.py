# backend/tests/test_agent_reconcile.py — 워크스페이스에서 유도하는 UI 이벤트.
#
# 여기 있는 검사들은 2026-08-18까지 tests/test_agent_tools.py에서 두 MCP 도구
# (`report_stage`, `handoff_prototype`)를 대상으로 돌던 것들이다. 도구가 훅으로
# 옮겨 가면서 **행동은 그대로, 트리거만 바뀌었다** — 그래서 검사도 옮겼다.
#
# 옮기면서 늘어난 것이 하나 있다: 도구는 "모델이 부른다"를 전제하므로 부르지 않는
# 경우를 검사할 수 없었다. 훅과 재조정은 부르지 않는 경우가 없으니, 이 파일은
# **파일만 있으면 화면이 맞는다**를 검사한다.
import json
from pathlib import Path

from aipds.agent import reconcile


def _payloads(events):
    return [json.loads(e.payload) for e in events]


# ---- 스테이지: aiplc-state.md에서 유도한다 (옛 report_stage) ----


def test_a_state_file_becomes_stage_events():
    md = ("# AI-PLC State\n\n"
          "- **Current Stage**: Envision\n\n"
          "## Stage Progress\n"
          "- [x] Workspace Detection\n"
          "- [ ] Envision\n"
          "- [ ] Solution Analysis\n")
    events, cursor = reconcile.stage_events(md, {})
    assert _payloads(events) == [
        {"stage": "Workspace Detection", "status": "completed", "summary": ""},
        # Current Stage가 가리키는 줄은 parse_state_file이 in_progress로 접는다.
        {"stage": "Envision", "status": "in_progress", "summary": ""},
        {"stage": "Solution Analysis", "status": "pending", "summary": ""},
    ]
    assert cursor["Envision"] == "in_progress"


def test_the_checklist_order_is_kept():
    """체크리스트 순서가 곧 방법론의 스테이지 순서다 — 정렬하면 사이드바가 룰과
    다른 순서로 쌓인다."""
    md = ("- **Current Stage**: Go-to-Market\n\n## Stage Progress\n"
          "- [x] Envision\n- [x] Product Strategy\n- [ ] Go-to-Market\n")
    events, _ = reconcile.stage_events(md, {})
    assert [p["stage"] for p in _payloads(events)] == [
        "Envision", "Product Strategy", "Go-to-Market"]


def test_an_unchanged_state_file_emits_nothing():
    """**diff가 이 설계의 요점이다.** 프론트가 `stage` 이벤트를 누적하므로
    (useWorkspaceStream.ts의 `[...prev, parsed]`) 같은 상태를 다시 흘리면 사이드바
    목록이 자란다. 옛 `report_stage` 도구는 모델이 같은 스테이지를 두 번 선언하면
    두 번 쐈다 — 이쪽이 오히려 조용하다."""
    md = "- **Current Stage**: Envision\n\n## Stage Progress\n- [ ] Envision\n"
    events, cursor = reconcile.stage_events(md, {})
    assert len(events) == 1
    again, cursor2 = reconcile.stage_events(md, cursor)
    assert again == []
    assert cursor2 == cursor


def test_only_the_stage_that_changed_is_emitted():
    first = "- **Current Stage**: Envision\n\n## Stage Progress\n- [ ] Envision\n- [ ] Solution Analysis\n"
    _, cursor = reconcile.stage_events(first, {})
    second = "- **Current Stage**: Solution Analysis\n\n## Stage Progress\n- [x] Envision\n- [ ] Solution Analysis\n"
    events, _ = reconcile.stage_events(second, cursor)
    assert _payloads(events) == [
        {"stage": "Envision", "status": "completed", "summary": ""},
        {"stage": "Solution Analysis", "status": "in_progress", "summary": ""},
    ]


def test_the_note_becomes_the_summary():
    md = ("- **Current Stage**: Envision\n\n## Stage Progress\n"
          "- [x] Envision — PR/FAQ 승인 완료\n")
    events, _ = reconcile.stage_events(md, {})
    assert _payloads(events)[0]["summary"] == "PR/FAQ 승인 완료"


def test_an_escaped_stage_name_is_unescaped():
    """2026-08-18 hpt-sarang: 모델이 `Prototype &amp; Validation`을 보냈다. 스테이지
    이름은 표시 문자열이 아니라 **키**이므로, `&amp;`가 박힌 줄은 다음의 올바른 줄과
    이름이 달라 체크라인이 하나 더 생긴다.

    옛 경로에서는 도구 인자와 파일 양쪽을 정규화해야 했는데, 지금은 파일이 유일한
    입력이라 읽기 쪽 한 번으로 끝난다(parsers/state.normalize_stage_name)."""
    md = ("- **Current Stage**: Prototype & Validation\n\n## Stage Progress\n"
          "- [ ] Prototype &amp; Validation\n")
    events, _ = reconcile.stage_events(md, {})
    payload = _payloads(events)[0]
    assert payload["stage"] == "Prototype & Validation"
    assert payload["status"] == "in_progress"


def test_an_escaped_then_clean_name_is_one_stage_not_two():
    """정규화가 커서에도 적용되므로 같은 스테이지로 합쳐진다 — 진행률이 같은
    스테이지를 두 번 세지 않는다."""
    escaped = "- **Current Stage**: X\n\n## Stage Progress\n- [ ] Prototype &amp; Validation\n"
    _, cursor = reconcile.stage_events(escaped, {})
    clean = "- **Current Stage**: X\n\n## Stage Progress\n- [ ] Prototype & Validation\n"
    events, cursor2 = reconcile.stage_events(clean, cursor)
    assert events == []
    assert list(cursor2) == ["Prototype & Validation"]


def test_a_missing_or_empty_state_file_changes_nothing():
    """없다는 사실로 화면을 지우지 않는다 — 에이전트가 파일을 잠깐 비웠다가 다시
    쓰는 중일 수 있다."""
    cursor = {"Envision": "in_progress"}
    for md in (None, "", "   \n"):
        events, out = reconcile.stage_events(md, cursor)
        assert events == []
        assert out == cursor


def test_a_state_file_with_no_checklist_emits_nothing():
    """Current Stage만 있고 체크리스트가 없으면 흘릴 항목이 없다. 여기서 항목을
    만들어 내면 룰이 정하지 않은 스테이지가 사이드바에 생긴다."""
    events, _ = reconcile.stage_events("- **Current Stage**: Envision\n", {})
    assert events == []


def test_read_state_returns_none_when_absent(tmp_path):
    assert reconcile.read_state(tmp_path) is None
    p = tmp_path / reconcile.STATE_KEY
    p.parent.mkdir(parents=True)
    p.write_text("- **Current Stage**: Envision\n", encoding="utf-8")
    assert "Envision" in (reconcile.read_state(tmp_path) or "")


# ---- 인계: build-instructions.md의 존재에서 유도한다 (옛 handoff_prototype) ----


def _spec(ws: Path, key: str) -> None:
    p = ws / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# spec", encoding="utf-8")


def test_the_single_prototype_layout_is_recognised(tmp_path):
    _spec(tmp_path, "aiplc-docs/discovery/prototype/prototype-spec.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototype/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert _payloads(events) == [{
        "slug": "prototype",
        "spec_path": "aiplc-docs/discovery/prototype/prototype-spec.md"}]
    assert cursor == {"prototype"}


def test_the_slugged_layout_is_recognised(tmp_path):
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-foo.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/build-instructions.md")
    events, _ = reconcile.prototype_events(tmp_path, set())
    assert _payloads(events) == [{
        "slug": "foo",
        "spec_path": "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-foo.md"}]


def test_build_instructions_without_a_spec_are_not_a_handoff(tmp_path):
    """Prototypes 탭은 **명세**에서 카드를 만든다(routes/prototypes.py의
    layout.discover). 빌드 지시만 있으면 사용자가 빈 탭을 본다 — 옛
    `handoff_prototype`이 명세 존재를 확인한 이유가 그것이고, 그 검사가 여기로
    옮겨 왔다."""
    _spec(tmp_path, "aiplc-docs/discovery/prototype/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert events == []
    assert cursor == set()


def test_a_spec_without_build_instructions_is_not_a_handoff(tmp_path):
    """명세만 쓴 시점(Step 1)에는 아직 인계가 아니다. 여기서 알리면 설계가 끝나기
    전에 카드가 뜬다."""
    _spec(tmp_path, "aiplc-docs/discovery/prototype/prototype-spec.md")
    events, _ = reconcile.prototype_events(tmp_path, set())
    assert events == []


def test_a_handoff_is_announced_once(tmp_path):
    """두 번 알리면 채팅에 카드가 두 장 뜬다. 빌드 지시를 고쳐 쓰는 것은 정상
    행동이므로 커서가 필요하다."""
    _spec(tmp_path, "aiplc-docs/discovery/prototype/prototype-spec.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototype/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert len(events) == 1
    again, _ = reconcile.prototype_events(tmp_path, cursor)
    assert again == []


def test_every_handed_off_prototype_is_announced(tmp_path):
    """Path B는 셋이다. 하나만 알리면 나머지 카드가 조용히 빠진다."""
    for slug in ("a", "b", "c"):
        _spec(tmp_path, f"aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md")
        _spec(tmp_path, f"aiplc-docs/discovery/prototypes/{slug}/build-instructions.md")
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert sorted(p["slug"] for p in _payloads(events)) == ["a", "b", "c"]
    assert cursor == {"a", "b", "c"}


def test_the_spec_path_is_the_real_one_not_a_computed_one(tmp_path):
    """`discover`가 디스크에서 찾은 경로를 그대로 싣는다. 슬러그로 경로를 다시
    조립하면 레이아웃 규약이 두 곳에 있게 된다(proto/layout.py가 단독 소유)."""
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-foo.md")
    _spec(tmp_path, "aiplc-docs/discovery/prototypes/foo/build-instructions.md")
    events, _ = reconcile.prototype_events(tmp_path, set())
    payload = _payloads(events)[0]
    assert (tmp_path / payload["spec_path"]).is_file()


def test_an_empty_workspace_is_quiet(tmp_path):
    events, cursor = reconcile.prototype_events(tmp_path, set())
    assert events == [] and cursor == set()


# ---- prototype_id_for: 경로 → id ----


def test_prototype_id_for_matches_both_layouts():
    assert reconcile.prototype_id_for(
        "aiplc-docs/discovery/prototype/build-instructions.md") == "prototype"
    assert reconcile.prototype_id_for(
        "aiplc-docs/discovery/prototypes/foo/build-instructions.md") == "foo"


def test_prototype_id_for_rejects_everything_else():
    """다른 파일에서 턴이 끊기면 에이전트가 작업 도중에 멈춘다 — 이 판정이 훅의
    턴 종료 조건이므로 좁아야 한다."""
    for rel in ("aiplc-docs/discovery/prototype/prototype-spec.md",
                "aiplc-docs/discovery/prototype/design-context.md",
                "aiplc-docs/audit.md",
                # 레이아웃 밖의 build-instructions.md는 인계가 아니다.
                "aiplc-docs/discovery/build-instructions.md",
                "aiplc-docs/discovery/envision/build-instructions.md",
                "build-instructions.md"):
        assert reconcile.prototype_id_for(rel) is None, rel


# ---- 문서: aiplc-docs/ 아래 산출물 쓰기에서 유도한다 (옛 submit_document) ----
#
# `submit_document`가 2026-08-21에 사라졌다. 남긴 근거는 "`version`과 '리뷰 준비됨
# vs 중간 저장'은 파싱이 아니라 판단"이었는데, 실제 지시(discovery-config/CLAUDE.md)는
# 판단을 요구하지 않았다 — 문서를 만들거나 갱신할 때마다 부르라고 했다. 판단이 없으면
# 신호는 "문서가 쓰였다"와 1:1이고, PostToolUse가 이미 그것을 본다.
#
# 게다가 그 도구는 **대부분 불리지 않았다**: 프론트가
# `useWorkspaceStream.ts:177`에 "에이전트는 대부분의 문서를 submit_document 없이
# file_write로만 만든다(실측: prfaq.md 등)"고 적고 우회로를 넣어 뒀다. 스테이지·인계와
# 같은 침묵의 세 번째 사례다.


def _doc(root: Path, rel: str, text: str = "# 문서\n본문\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_a_document_write_becomes_a_document_event(tmp_path):
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md")
    events, _ = reconcile.document_events(tmp_path, {})
    assert [e.kind for e in events] == ["document"]
    assert _payloads(events) == [
        {"path": "aiplc-docs/discovery/prfaq.md", "version": "1", "summary": ""}]


def test_the_record_keeping_files_are_not_documents(tmp_path):
    """감사·상태·질문 파일은 문서 패널이 따라갈 산출물이 아니다.

    프론트의 `isDocPath`와 같은 판정이다(useWorkspaceStream.ts) — 질문 파일이
    여기 끼면 한 화면에 같은 질문의 두 버전이 나란히 뜬다.
    """
    for rel in ("aiplc-docs/audit.md",
                "aiplc-docs/aiplc-state.md",
                "aiplc-docs/discovery/envision/pain-point-questions.md",
                "aiplc-docs/discovery/notes.txt",
                "outside/other.md"):
        _doc(tmp_path, rel)
    events, cursor = reconcile.document_events(tmp_path, {})
    assert events == [] and cursor == {}


def test_the_same_content_is_announced_once(tmp_path):
    """커서가 없으면 매 쓰기마다 배너가 다시 뜬다 — 에이전트가 같은 문서를 한 턴에
    여러 번 쓰는 것은 정상 동작이다(stage_events가 diff를 쓰는 것과 같은 이유)."""
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md")
    first, cursor = reconcile.document_events(tmp_path, {})
    again, cursor2 = reconcile.document_events(tmp_path, cursor)
    assert len(first) == 1
    assert again == [] and cursor2 == cursor


def test_a_changed_document_gets_the_next_version(tmp_path):
    """배너의 닫기가 `version`을 기억하므로(page.tsx의 dismissedDocVersion) 갱신마다
    값이 달라져야 한다 — 같으면 한 번 닫은 뒤 다시 뜨지 않는다."""
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md", "# 문서\n초안\n")
    _, cursor = reconcile.document_events(tmp_path, {})
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md", "# 문서\n고친 본문\n")
    events, _ = reconcile.document_events(tmp_path, cursor)
    assert _payloads(events) == [
        {"path": "aiplc-docs/discovery/prfaq.md", "version": "2", "summary": ""}]


def test_an_empty_document_is_not_announced(tmp_path):
    """옛 도구가 빈 파일 선언을 거부한 이유를 유지한다 — 사용자가 "작성됐습니다"를
    읽으면서 빈 문서 패널을 본다."""
    _doc(tmp_path, "aiplc-docs/discovery/prfaq.md", "   \n\n")
    events, cursor = reconcile.document_events(tmp_path, {})
    assert events == [] and cursor == {}
