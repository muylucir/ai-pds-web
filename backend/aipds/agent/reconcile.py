# backend/aipds/agent/reconcile.py — 워크스페이스에서 UI 이벤트를 **유도**한다.
#
# **왜 이 모듈이 생겼는가.** 스테이지 사이드바와 Prototypes 카드는 원래 모델이
# 부르는 MCP 도구(`report_stage`, `handoff_prototype`)가 만들었다. 도구는 모델이
# 부르지 않으면 아무 일도 일어나지 않고, 그 침묵이 두 번 실측됐다:
#
#   - 2026-08-18 test123456: PostToolUse 훅이 질문 파일 쓰기에서 턴을 끝내자 같은
#     메시지에 배치된 `report_stage`가 실행되지 않았다. `aiplc-state.md`가 없어
#     배지가 프로젝트 내내 비었고, 재개 턴은 "멈춘 지점부터"라 회수 계기가 없었다.
#   - 2026-08-17 keumkang-v5: `handoff_prototype`이 없던 시절 에이전트가 자격증명을
#     묻고 선행 조건을 나열했다. 탭 안내는 0회였다.
#
# 그래서 판정 기준을 "모델이 선언했는가"에서 **"디스크가 무엇을 말하는가"**로
# 옮긴다. 파일은 유실되지 않는다 — 배치 드롭도, 턴 종료도, 모델의 건망증도 파일을
# 지우지 못한다.
#
# **세 번째 도구도 여기로 왔다(2026-08-21).** 이 절은 `submit_document`가 남는 이유를
# "그 `version`과 '리뷰 준비됨 vs 중간 저장'은 파싱이 아니라 판단"이라고 적고 있었다.
# 그 근거가 틀렸다 — **실제 지시가 그 판단을 요구하지 않았다.** discovery-config는
# "문서를 만들거나 갱신할 때마다 부르라"고 했고, 판단이 없으면 신호는 "문서가 쓰였다"와
# 1:1이다. 그리고 그것은 PostToolUse가 이미 보는 것이었다.
#
# 침묵도 똑같이 실측돼 있었다. 프론트가 우회로와 함께 적어 뒀다:
# "에이전트는 대부분의 문서를 submit_document 없이 file_write로만 만든다(실측:
# prfaq.md 등)" — useWorkspaceStream.ts:177. 위 두 사례와 같은 부류의 세 번째다.
#
# 남은 `version`은 판단이 아니라 **셈**이다: 내용 해시로 "바뀌었나"를 답하고 서수로
# "몇 번째인가"를 센다(`document_events`). 모델이 짓던 문자열보다 정확하다 — 모델은
# 같은 문서에 `v1`을 두 번 선언할 수 있었고 그것을 막는 장치가 없었다.
#
# **여기 있는 것과 없는 것.** 워크스페이스에서 유도되는 신호만 여기 있다. 남은 커스텀
# 도구는 `build_complete` 하나이고 그것은 같은 기준을 통과한다 — 빌드의 마지막 Write는
# 다른 Write와 구별되지 않으므로 "끝났다"가 파일에서 유도되지 않는다(proto/tools.py).
#
# **왜 diff인가.** agent/tools.py의 옛 헤더가 "상태 파일 쓰기에서 역추론하면 한 턴에
# 여러 번 갱신될 때 UI가 흔들린다"고 걱정했다. 그 걱정은 정당했지만 해법은 도구가
# 아니라 diff다: 프론트가 `stage` 이벤트를 **누적**하므로(useWorkspaceStream.ts:189
# `[...prev, parsed]`) 같은 상태를 다시 흘리면 목록이 자란다. 상태가 실제로 바뀐
# 스테이지만 흘리면 그 문제가 사라지고, 옛 도구보다 오히려 조용하다 — 도구는 모델이
# 같은 스테이지를 두 번 선언하면 두 번 쐈다.
#
# **순수 함수 + 명시적 커서.** 드라이버가 커서(`last`, `announced`)를 들고 있고 이
# 모듈은 그것을 받아 새 커서를 돌려준다. 모듈 전역 상태를 두지 않는 이유는 드라이버가
# 프로젝트당 하나이고 테스트가 커서를 직접 넣어 diff 경로를 검사해야 하기 때문이다.
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from aipds.models import AgentEvent
from aipds.parsers.state import parse_state_file
from aipds.proto import layout

_log = logging.getLogger("aipds.agent")

#: 산출물 루트. 상류 룰이 정한 이름이고 `STATE_KEY`도 이 아래에 있다.
DOCS_ROOT = "aiplc-docs"

#: 상류 룰이 정한 상태 파일. `core-workflow.md`의 트리와
#: `prototype-validation.md` Step 10이 이 경로를 쓴다.
STATE_KEY = "aiplc-docs/aiplc-state.md"

#: Step 3의 마지막 산출물 이름. `prototype-validation.md:170`이 단수 경로로
#: 선언하고(`aiplc-docs/discovery/prototype/build-instructions.md`), Path B에서는
#: 같은 이름이 슬러그 디렉터리 아래 온다 — 경로 조립은 `layout.artifact_dir`이
#: 단독으로 소유하므로 여기서는 **파일 이름만** 안다.
BUILD_INSTRUCTIONS = "build-instructions.md"


def stage_events(markdown: str | None,
                 last: dict[str, str]) -> tuple[list[AgentEvent], dict[str, str]]:
    """`aiplc-state.md` 전문 → 상태가 **바뀐** 스테이지의 `stage` 이벤트.

    `last`는 "이 스테이지를 이 상태로 이미 흘렸다"는 커서다. 돌려주는 dict가 새
    커서이고, 호출부가 그것으로 교체한다.

    파일 순서를 유지한다. 체크리스트 순서가 곧 방법론의 스테이지 순서이므로
    (`core-workflow.md`의 Stage Progress), 정렬하면 사이드바가 룰과 다른 순서로
    쌓인다.

    **`current_stage`를 별도로 만들지 않는다.** `parse_state_file`이 이미 그것을
    체크리스트의 한 줄에 `in_progress`로 접어 넣는다(정확 일치 우선, 없으면 최장
    부분 일치). 그 접기 밖에 있는 `current_stage`는 체크리스트에 줄이 없는
    이름이라는 뜻이고, 그때 항목을 하나 만들어 내면 룰이 정하지 않은 스테이지가
    사이드바에 생긴다 — 파서가 이미 판단한 것을 여기서 뒤집지 않는다.

    빈 파일/None은 이벤트 없음이다. 그것은 "스테이지가 없다"는 사실이고, 없다는
    사실로 화면을 지우지는 않는다(에이전트가 파일을 잠깐 비웠다가 다시 쓰는 중일
    수 있다).
    """
    if not markdown or not markdown.strip():
        return [], last
    try:
        state = parse_state_file(markdown)
    except Exception:
        # 손상된 상태 파일로 턴을 죽이지 않는다 — 배지가 낡는 것이 턴 실패보다 낫고,
        # 다음 쓰기가 다시 기회를 준다.
        _log.exception("could not parse %s — leaving the stage badges as they are",
                       STATE_KEY)
        return [], last
    events: list[AgentEvent] = []
    cursor = dict(last)
    for stage in state.stages:
        if cursor.get(stage.name) == stage.status:
            continue
        cursor[stage.name] = stage.status
        events.append(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage.name, "status": stage.status,
             "summary": stage.note or ""}, ensure_ascii=False)))
    return events, cursor


def _discovery_keys(workspace: Path) -> list[str]:
    """`aiplc-docs/discovery/` 아래 파일을 워크스페이스 상대 POSIX 경로로 걷는다.

    `layout.discover`가 이 모양을 받도록 설계돼 있어(proto/layout.py) 목록
    라우트와 **같은 함수**로 판정할 수 있다. S3가 아니라 로컬을 읽는 이유는
    에이전트가 방금 Write한 파일을 곧바로 판정해야 하기 때문이다 — 그 시점의
    정본은 디스크다(옛 `tools._discovery_keys`가 같은 판단을 기록해 뒀다).
    """
    base = workspace / layout.DISCOVERY_PREFIX
    if not base.is_dir():
        return []
    return sorted(p.relative_to(workspace).as_posix()
                  for p in base.rglob("*") if p.is_file())


def prototype_id_for(rel: str) -> str | None:
    """이 경로가 어떤 프로토타입의 `build-instructions.md`라면 그 id, 아니면 None.

    `layout.artifact_dir`의 역이다. 경로를 직접 파싱하지 않고 **후보 id로 조립해
    비교**한다 — 레이아웃 규약은 그 모듈이 단독 소유하고, 여기서 정규식을 한 벌 더
    쓰면 규칙이 두 곳에 있게 된다(layout.py 헤더의 "네 곳에 복제" 비용이 그것이다).

    후보는 경로에서 온다: 마지막 디렉터리 이름이 id 후보다(단수는 `prototype`,
    슬러그는 `{slug}`). 그것을 `artifact_dir`에 넣어 원래 경로가 나오면 맞다.
    """
    p = Path(rel)
    if p.name != BUILD_INSTRUCTIONS:
        return None
    candidate = p.parent.name
    if not candidate:
        return None
    if f"{layout.artifact_dir(candidate)}/{BUILD_INSTRUCTIONS}" != rel:
        return None
    return candidate


def handed_off(workspace: Path) -> dict[str, str]:
    """`build-instructions.md`가 **디스크에 있는** 프로토타입 → 그 명세 경로.

    명세와 빌드 지시 **둘 다** 있어야 인계로 본다. 명세가 없으면 Prototypes 탭이
    카드를 만들지 못하므로(routes/prototypes.py가 `layout.discover`로 목록을 만든다)
    사용자가 빈 탭을 본다 — 옛 `handoff_prototype`이 명세 존재를 확인한 이유가
    그것이고, 그 검사가 여기로 옮겨 온 것이다.
    """
    keys = _discovery_keys(workspace)
    specs = layout.discover(keys)
    present = {k for k in keys if prototype_id_for(k) is not None}
    return {pid: spec for pid, spec in specs.items()
            if f"{layout.artifact_dir(pid)}/{BUILD_INSTRUCTIONS}" in present}


def prototype_events(workspace: Path,
                     announced: set[str]) -> tuple[list[AgentEvent], set[str]]:
    """아직 알리지 않은 인계를 `prototype_ready` 이벤트로.

    `announced`가 커서다 — 같은 프로토타입을 두 번 알리면 채팅에 카드가 두 장 뜬다.
    """
    events: list[AgentEvent] = []
    cursor = set(announced)
    for pid, spec in sorted(handed_off(workspace).items()):
        if pid in cursor:
            continue
        cursor.add(pid)
        events.append(AgentEvent(kind="prototype_ready", payload=json.dumps(
            {"slug": pid, "spec_path": spec}, ensure_ascii=False)))
    return events, cursor


#: 문서가 아닌 기록물. 상류 룰이 요구하는 파일들이고 문서 패널이 따라갈 산출물이
#: 아니다 — 질문 파일이 여기 끼는 이유는 프론트가 적어 뒀다(useWorkspaceStream.ts):
#: 답변 화면은 우측 패널의 QuestionForm이므로, 마크다운까지 문서로 띄우면 한 화면에
#: 같은 질문의 두 버전이 뜨고 그 둘은 애초에 일치할 수 없다.
_RECORD_KEEPING = ("audit.md", "aiplc-state.md")
_QUESTION_SUFFIX = "-questions.md"


def is_document(rel: str) -> bool:
    """이 경로가 문서 패널이 따라갈 산출물인가.

    프론트의 `isDocPath`와 **같은 판정**이다. 두 벌인 것은 의도다: 프론트 쪽은
    `file_changed`에 걸린 백스톱으로 남아 있고(훅이 못 보는 Bash 경유 쓰기), 이쪽이
    `document` 이벤트의 주 경로다. 어긋나도 해롭지 않다 — 둘 다 activeDoc을 같은
    방향으로만 움직인다.
    """
    if not rel.startswith(f"{DOCS_ROOT}/") or not rel.endswith(".md"):
        return False
    name = rel.rsplit("/", 1)[-1]
    return name not in _RECORD_KEEPING and not name.endswith(_QUESTION_SUFFIX)


def document_events(workspace: Path,
                    seen: dict[str, tuple[int, str]],
                    ) -> tuple[list[AgentEvent], dict[str, tuple[int, str]]]:
    """내용이 **바뀐** 산출물의 `document` 이벤트. 옛 `submit_document`를 대체한다.

    `seen`이 커서다: 경로 → (버전, 내용 해시). `stage_events`와 같은 이유로 diff다 —
    에이전트가 한 턴에 같은 문서를 여러 번 쓰는 것은 정상 동작이고, 매번 흘리면
    갱신 배너가 그만큼 다시 뜬다.

    **버전이 왜 필요한가.** 배너의 닫기 버튼이 `version`을 기억하므로
    (page.tsx의 `dismissedDocVersion`) 갱신마다 값이 달라져야 한다. 값이 같으면 한 번
    닫은 뒤 그 문서의 어떤 갱신도 다시 알리지 못한다. 그래서 해시로 "바뀌었나"를
    판정하고 서수로 "몇 번째인가"를 센다.

    **한계: 서수는 프로세스 생애 안에서만 이어진다.** 커서는 드라이버가 들고 있고
    (`_stage_status`·`_handed_off`와 같은 인메모리 커서다) 백엔드 재시작 시 1로
    돌아간다. 배너의 `dismissedDocVersion`도 페이지 상태라 새로고침에서 함께 비므로
    흔한 경우에는 둘이 같이 리셋된다.

    빈 파일은 알리지 않는다 — 옛 도구가 빈 선언을 거부한 이유가 그대로다: 사용자가
    "작성됐습니다"를 읽으면서 빈 문서 패널을 본다.
    """
    events: list[AgentEvent] = []
    cursor = dict(seen)
    root = workspace / DOCS_ROOT
    if not root.is_dir():
        return events, cursor
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(workspace).as_posix()
        if not is_document(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = cursor.get(rel)
        if previous is not None and previous[1] == digest:
            continue
        version = (previous[0] if previous else 0) + 1
        cursor[rel] = (version, digest)
        events.append(AgentEvent(kind="document", payload=json.dumps(
            {"path": rel, "version": str(version), "summary": ""},
            ensure_ascii=False)))
    return events, cursor


def read_state(workspace: Path) -> str | None:
    """워크스페이스의 상태 파일 전문. 없으면 None.

    읽기 실패도 None이다 — 재조정은 백스톱이고, 그것이 턴을 실패시키면 백스톱이
    아니라 새 실패 원인이 된다.
    """
    try:
        p = workspace / STATE_KEY
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except OSError:
        _log.exception("could not read %s", STATE_KEY)
        return None
