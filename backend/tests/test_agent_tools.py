from pathlib import Path
import pytest
from pathfinder.models import AgentEvent
from pathfinder.agent.tools import build_tools


async def _noop_publish(rel: str) -> None:
    """게시는 이 파일들의 관심사가 아니다 — build_tools가 게시자를 **필수**로
    받는 이유는 새 호출부가 조용히 빠뜨리지 못하게 하는 것이고, 그 계약은
    test_agent_tools의 게시 테스트와 test_workspace_sync가 지킨다."""
    return None


def _tool_by_name(tools, name):
    # claude_agent_sdk의 SdkMcpTool은 .name을 노출하고, .handler가 실제 async
    # 구현이다 — strands @tool 객체(.tool_name, 직접 호출 가능)와 다르다.
    return next(t for t in tools if getattr(t, "name", "") == name)


def _tools(workspace):
    emitted = []
    tools = build_tools(str(workspace), emitted.append, publish=_noop_publish)
    return {name: _tool_by_name(tools, name)
            for name in ("report_stage", "submit_document",
                         "handoff_prototype")}, emitted


async def _call(tool, **kwargs):
    """SdkMcpTool.handler는 async이고 단일 dict 인자를 받는다."""
    result = await tool.handler(kwargs)
    return result["content"][0]["text"]


async def test_report_stage_rejects_invalid_status(tmp_path):
    tools, _ = _tools(tmp_path / "ws")
    out = await _call(tools["report_stage"], stage="Envision", status="bogus")
    assert "invalid status" in out


async def test_report_stage_writes_state_file(tmp_path):
    from pathfinder.parsers.state import parse_state_file
    ws = tmp_path / "ws"; ws.mkdir()
    tools, _ = _tools(ws)
    await _call(tools["report_stage"], stage="Envision", status="in_progress", summary="시작")
    state_file = ws / "aiplc-docs" / "aiplc-state.md"
    assert state_file.is_file()
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.current_stage == "Envision"
    await _call(tools["report_stage"], stage="Envision", status="completed", summary="끝")
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.stages[0].status == "completed"


async def test_report_stage_survives_state_write_failure(tmp_path, monkeypatch):
    # fail-soft: 상태 파일 upsert가 터져도 이벤트/반환은 정상.
    ws = tmp_path / "ws"; ws.mkdir()
    emitted = []
    from pathfinder.agent import tools as tools_mod
    monkeypatch.setattr(tools_mod, "upsert_stage",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tools = {t.name: t for t in tools_mod.build_tools(str(ws), emitted.append, publish=_noop_publish)}
    out = await _call(tools["report_stage"], stage="Envision", status="in_progress")
    assert "stage recorded" in out
    assert emitted and emitted[0].kind == "stage"


# ---- submit_document must not declare a document that isn't on disk ----

def _ws_and_tools(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tools, emitted = _tools(ws)
    return ws, tools, emitted


async def test_submit_document_emits_when_the_file_exists(tmp_path):
    ws, tools, emitted = _ws_and_tools(tmp_path)
    doc = ws / "aiplc-docs" / "discovery" / "discovery-document.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# 내용", encoding="utf-8")

    result = await _call(tools["submit_document"],
                         path="aiplc-docs/discovery/discovery-document.md",
                         version="v1", summary="요약")

    assert "submitted" in result
    docs = [e for e in emitted if e.kind == "document"]
    assert len(docs) == 1


async def test_submit_document_refuses_a_path_that_was_never_written(tmp_path):
    """The decoupling that made a real bug invisible: this tool only emitted an
    event, so an agent that called it without a preceding file write produced a
    chat message saying the document was created, a dropdown entry for it, and
    no document. The event is the UI's source of truth for "a document is
    ready", so it must not fire for a file that does not exist."""
    ws, tools, emitted = _ws_and_tools(tmp_path)

    result = await _call(tools["submit_document"],
                         path="aiplc-docs/discovery/discovery-document.md",
                         version="v1")

    assert "document" not in [e.kind for e in emitted]
    assert "저장" in result or "Write" in result  # tells the agent what to do instead


async def test_submit_document_refuses_an_empty_file(tmp_path):
    """A zero-byte or whitespace-only file is the same failure wearing a
    different hat -- the panel would render "문서 내용이 아직 비어 있습니다"
    while the chat claimed success."""
    ws, tools, emitted = _ws_and_tools(tmp_path)
    doc = ws / "aiplc-docs" / "empty.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("   \n", encoding="utf-8")

    result = await _call(tools["submit_document"], path="aiplc-docs/empty.md", version="v1")

    assert "document" not in [e.kind for e in emitted]
    assert "비어" in result or "empty" in result.lower()


async def test_submit_document_rejects_a_path_escaping_the_workspace(tmp_path):
    ws, tools, emitted = _ws_and_tools(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("nope", encoding="utf-8")

    result = await _call(tools["submit_document"], path="../secret.md", version="v1")

    assert "document" not in [e.kind for e in emitted]
    assert "escape" in result.lower() or "경로" in result


# ---- handoff_prototype — 빌드로 넘기는 행동 ----
# 2026-08-17: Path A.1의 Step 3은 "Build Prototype"이고 상류 Step 4~11은 돌아가는
# 프로토타입을 전제한다. Pathfinder는 빌드를 Prototypes 탭이 하므로 그 자리에서
# 흐름이 끊겼다 — 그런데 **금지만 있고 대체 행동이 없어서** 에이전트가 즉흥
# 대응했다(실측 keumkang-v5: 자격증명 점검 → API 키 요구 → 선행 조건 3건 나열,
# Prototypes 탭 안내는 한 번도 없었다).
#
# report_stage가 있어서 상태 파일을 손으로 안 쓰고, submit_document가 있어서 문서
# 준비를 선언하는 것과 같은 규율이다: **도구가 행동을 만든다.** 이 도구가
# "빌드로 넘어가기"라는 하고 싶은 일에 대응한다.

async def test_handoff_refuses_when_the_spec_is_not_there(tmp_path):
    """submit_document와 같은 규율 — 도구가 거짓을 선언할 수 없다. 명세가 없으면
    Prototypes 탭에 카드가 없으므로, 넘겼다고 말하면 사용자가 빈 탭을 본다."""
    tools, emitted = _tools(tmp_path / "ws")
    out = await _call(tools["handoff_prototype"], slug="prototype")
    assert "거부" in out or "Refused" in out
    assert not emitted, "거부했는데 이벤트를 흘리면 화면에 카드가 뜬다"


async def test_handoff_accepts_the_single_prototype_layout(tmp_path):
    """Path A.1의 명세 경로다(proto/layout.py의 SINGLE_SPEC_KEY)."""
    ws = tmp_path / "ws"
    spec = ws / "aiplc-docs" / "discovery" / "prototype" / "prototype-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, emitted = _tools(ws)
    out = await _call(tools["handoff_prototype"], slug="prototype")

    assert "Prototypes" in out
    kinds = [e.kind for e in emitted]
    assert "prototype_ready" in kinds, kinds


async def test_handoff_accepts_the_slugged_layout(tmp_path):
    ws = tmp_path / "ws"
    spec = (ws / "aiplc-docs" / "discovery" / "prototypes" / "maint"
            / "PROTOTYPE-maint.md")
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, emitted = _tools(ws)
    await _call(tools["handoff_prototype"], slug="maint")

    payloads = [e.payload for e in emitted if e.kind == "prototype_ready"]
    assert payloads and "maint" in payloads[0]


async def test_handoff_tells_the_agent_to_stop_and_not_ask_for_credentials(tmp_path):
    """반환 문자열이 다음 행동을 지정한다. 이것이 없으면 에이전트가 Step 4로
    계속 가거나(돌아가는 프로토타입이 없으니 실패한다) 자격증명을 묻는다 —
    프로젝트가 모델과 자격증명을 이미 갖고 있는데도."""
    ws = tmp_path / "ws"
    spec = ws / "aiplc-docs" / "discovery" / "prototype" / "prototype-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, _ = _tools(ws)
    out = await _call(tools["handoff_prototype"], slug="prototype")

    assert "턴" in out or "end your turn" in out.lower()
    assert "자격증명" in out or "credential" in out.lower()


async def test_handoff_refuses_a_path_escaping_slug(tmp_path):
    tools, emitted = _tools(tmp_path / "ws")
    out = await _call(tools["handoff_prototype"], slug="../../etc")
    assert "거부" in out or "Refused" in out
    assert not emitted


# ---- 지어낸 슬러그 (2026-08-18 실측: hpt-sarang) ----
# 단일 해법으로 완주한 프로젝트에서 에이전트가 제품명으로
# `claim-appeal-evidence-assistant`를 만들어 넘겼다. 옛 구현은 `spec_key(slug)`로
# **없는 게 당연한 경로**를 계산해 지목하며 "룰이 정한 자리에 명세를 먼저 쓰라"고
# 했고, 에이전트는 그 지시대로 그 자리에 파일을 만들어 검사를 통과시켰다. 명세가
# 둘로 보이니 카드도 둘이 떴다(routes/prototypes.py가 파일에서 카드를 파생한다).
#
# 슬러그는 이름이 아니라 **형제가 여럿일 때의 구별자**다(core-workflow.md의 top-3
# 레이아웃). 형제가 없는 프로젝트에서 슬러그를 지어내면 없는 형제가 하나 생긴다.

async def test_handoff_refuses_an_invented_slug_and_names_the_real_id(tmp_path):
    """거부만으로는 부족하다 — 옛 문구도 거부는 했다. **고를 것을 보여줘야** 한다."""
    ws = tmp_path / "ws"
    spec = ws / "aiplc-docs" / "discovery" / "prototype" / "prototype-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, emitted = _tools(ws)
    out = await _call(tools["handoff_prototype"],
                      slug="claim-appeal-evidence-assistant")

    assert "거부" in out or "Refused" in out
    assert not emitted, "거부했는데 이벤트를 흘리면 카드가 뜬다"
    assert "prototype" in out, f"고를 수 있는 id를 알려주지 않는다: {out}"
    # 옛 문구의 실제 피해: 슬러그 경로를 지목해 파일 생성을 지시했다.
    assert "PROTOTYPE-claim-appeal-evidence-assistant" not in out, (
        "없는 경로를 지목하면 에이전트가 그 자리에 파일을 만든다")


async def test_handoff_refusal_does_not_ask_for_a_new_spec_when_one_exists(tmp_path):
    """명세가 이미 있으면 "명세를 먼저 써라"는 **틀린 지시**다 — 에이전트는 이미
    썼고, 어긋난 쪽은 슬러그다."""
    ws = tmp_path / "ws"
    spec = ws / "aiplc-docs" / "discovery" / "prototype" / "prototype-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, _ = _tools(ws)
    out = await _call(tools["handoff_prototype"], slug="made-up")

    assert "쓴 뒤" not in out and "Write the spec" not in out


async def test_handoff_lists_every_candidate_in_the_multi_prototype_layout(tmp_path):
    """Path A.2/B는 top 3다. 후보가 여럿이면 셋 다 보여야 한다 — 하나만 알려주면
    에이전트가 나머지를 못 넘기거나 다시 지어낸다."""
    ws = tmp_path / "ws"
    base = ws / "aiplc-docs" / "discovery" / "prototypes"
    for slug in ("triage", "maint", "billing"):
        spec = base / slug / f"PROTOTYPE-{slug}.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("# 명세", encoding="utf-8")

    tools, emitted = _tools(ws)
    out = await _call(tools["handoff_prototype"], slug="invented")

    assert not emitted
    for slug in ("triage", "maint", "billing"):
        assert slug in out, f"{slug}가 후보 목록에 없다: {out}"


async def test_handoff_asks_for_a_spec_only_when_there_is_none(tmp_path):
    """후보가 비어 있을 때는 정말로 파일을 써야 한다 — 그 경로는 남는다."""
    tools, _ = _tools(tmp_path / "ws")
    out = await _call(tools["handoff_prototype"], slug="prototype")
    assert "쓴 뒤" in out or "Write the spec" in out


async def test_handoff_reports_the_real_spec_path_not_a_computed_one(tmp_path):
    """이벤트의 `spec_path`는 `discover`가 찾은 **실제 키**다. 계산한 경로를 실으면
    단일 레이아웃에서 빌드가 없는 파일을 읽으러 간다."""
    ws = tmp_path / "ws"
    spec = ws / "aiplc-docs" / "discovery" / "prototype" / "prototype-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, emitted = _tools(ws)
    await _call(tools["handoff_prototype"], slug="prototype")

    payload = next(e.payload for e in emitted if e.kind == "prototype_ready")
    assert "aiplc-docs/discovery/prototype/prototype-spec.md" in payload


# ---- report_stage도 쓰기 직후 게시한다 ----
# 2026-08-18 실측: 실제 턴에서 `file_changed`가 온 직후 그 문서를 읽으면 되는데
# (Write 도구 경로는 PostToolUse 훅이 게시한다) `aiplc-docs/aiplc-state.md`만
# 404였다. 이 도구는 파일을 **로컬에 직접 쓰고** emit으로 알리므로 그 훅을 지나지
# 않고, 그래서 게시 계약을 빠뜨렸다 — 진행률 사이드바가 턴 종료까지 낡은 상태를
# 읽었다(UI의 읽기 경로는 전부 정본이다).
#
# 게시자를 **필수 인자로** 받는다. 기본값을 no-op로 두면 새 호출부가 조용히
# 빠뜨리고, 그 실패는 "화면이 낡아 보인다"로만 나타난다.

async def test_report_stage_publishes_the_state_file(tmp_path):
    published: list[str] = []

    async def publish(rel: str) -> None:
        published.append(rel)

    tools = {t.name: t for t in build_tools(
        str(tmp_path / "ws"), lambda e: None, publish=publish)}
    await _call(tools["report_stage"], stage="Envision", status="in_progress")
    assert published == ["aiplc-docs/aiplc-state.md"]


async def test_report_stage_survives_a_failing_publisher(tmp_path):
    """게시 실패가 스테이지 기록을 막지 않는다 — 화면 이벤트가 우선이라는
    기존 fail-soft 규율과 같다(그 위의 upsert 실패 처리 참조)."""
    async def publish(rel: str) -> None:
        raise RuntimeError("s3 down")

    emitted: list[AgentEvent] = []
    tools = {t.name: t for t in build_tools(
        str(tmp_path / "ws"), emitted.append, publish=publish)}
    out = await _call(tools["report_stage"], stage="Envision", status="completed")
    assert "stage recorded" in out
    assert any(e.kind == "stage" for e in emitted)


async def test_the_state_file_is_published_with_its_new_content(tmp_path):
    """게시가 upsert **뒤에** 일어나야 한다 — 앞이면 갱신 전 내용이 정본에 간다."""
    ws = tmp_path / "ws"
    seen: list[str] = []

    async def publish(rel: str) -> None:
        seen.append((ws / rel).read_text(encoding="utf-8"))

    tools = {t.name: t for t in build_tools(str(ws), lambda e: None,
                                            publish=publish)}
    await _call(tools["report_stage"], stage="Envision", status="completed")
    assert seen and "Envision" in seen[0]


# ---- report_stage는 모델이 보낸 이름을 키로 쓰기 전에 정규화한다 ----
# 2026-08-18 실측(hpt-sarang): 모델이 `"stage": "Prototype &amp; Validation"`을
# 보냈다. 같은 호출의 summary와 직전 호출은 `&`가 정상이었다 — 그 필드 하나의
# 이스케이프다. 이름은 키이므로 표시·매칭·집계가 함께 깨진다.

async def test_report_stage_unescapes_the_stage_name_in_the_state_file(tmp_path):
    ws = tmp_path / "ws"
    tools, _ = _tools(ws)

    await _call(tools["report_stage"], stage="Prototype &amp; Validation",
                status="in_progress")

    md = (ws / "aiplc-docs" / "aiplc-state.md").read_text(encoding="utf-8")
    assert "&amp;" not in md, md
    assert "Prototype & Validation" in md


async def test_report_stage_unescapes_the_stage_name_in_the_event(tmp_path):
    """이벤트와 파일이 같은 이름을 봐야 사이드바와 체크리스트가 어긋나지 않는다."""
    tools, emitted = _tools(tmp_path / "ws")

    await _call(tools["report_stage"], stage="Prototype &amp; Validation",
                status="in_progress")

    payload = next(e.payload for e in emitted if e.kind == "stage")
    assert "&amp;" not in payload, payload
    assert "Prototype & Validation" in payload


async def test_escaped_then_clean_call_updates_one_line_not_two(tmp_path):
    """정규화가 막는 실제 피해. 이스케이프된 이름을 그대로 쓰면 다음의 올바른
    호출이 이름이 다르다고 판단해 체크라인을 **하나 더** 만들고, 진행률이 같은
    스테이지를 두 번 센다."""
    ws = tmp_path / "ws"
    tools, _ = _tools(ws)

    await _call(tools["report_stage"], stage="Prototype &amp; Validation",
                status="in_progress")
    await _call(tools["report_stage"], stage="Prototype & Validation",
                status="completed")

    md = (ws / "aiplc-docs" / "aiplc-state.md").read_text(encoding="utf-8")
    assert md.count("Prototype & Validation") == 2, md   # Current Stage 1 + 체크라인 1
    assert "- [x] Prototype & Validation" in md
    assert "- [ ] Prototype & Validation" not in md
