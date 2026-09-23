# backend/tests/test_routes_turns.py
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.workspace import Workspace
from aipds.models import AgentEvent
from aipds.turn_marker import TURN_MARKER_KEY
from fakes.in_memory_s3 import FakeS3Store

client: TestClient


@pytest.fixture(autouse=True)
def _live_client():
    """요청 사이에 이벤트 루프를 유지하는 클라이언트.

    턴은 요청이 아니라 서버 작업이다(aipds/turn_job.py). `with` 없는 TestClient는
    요청마다 루프를 새로 열고 닫으므로, POST가 시작한 턴이 다음 요청 전에 루프와
    함께 사라진다 — 배포에서는 일어나지 않는 일이다.
    """
    global client
    with TestClient(app_module.app) as live:
        client = live
        yield

# Demo questions payload — a structured-demo shape so the answers/pending route
# tests can arm a pending interrupt with no AWS.
_DEMO_QUESTIONS = {
    "name": "pain-point-questions",
    "preamble": "데모 시나리오입니다.",
    "parse_ok": True,
    "raw_markdown": None,
    "questions": [
        {"number": 1, "category": "고객", "text": "주요 사용자는?", "answer": None,
         "options": [{"letter": "A", "text": "PM", "is_other": False, "recommended": True}]},
    ],
}


def _structured_first_turn(text):
    payload = json.dumps({"interrupt_id": "local-i-1", "questions": _DEMO_QUESTIONS},
                         ensure_ascii=False)
    return [
        AgentEvent(kind="message", text=f"'{text}' 요청을 받았습니다."),
        AgentEvent(kind="stage", payload=json.dumps(
            {"stage": "Envision", "status": "in_progress", "summary": "질문 생성"},
            ensure_ascii=False)),
        AgentEvent(kind="questions", payload=payload),
        AgentEvent(kind="done"),
    ]


class ScriptRunner:
    """send_message/send_answers/pending만 필요한 라우트 테스트용 러너.

    script 미지정 시 구조화 데모 흐름을 흉내낸다: send_message가
    questions 이벤트로 pending을 무장하고, send_answers가 document+done을 낸다."""
    def __init__(self, script=None):
        self._script = script or _structured_first_turn
        self._pending_payload = None
        self.interrupts = 0

    async def send_message(self, text):
        for e in self._script(text):
            if e.kind == "questions":
                self._pending_payload = e.payload
            yield e

    async def send_answers(self, answers):
        if self._pending_payload is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._pending_payload = None
        summary = ", ".join(f"{k}={v}" for k, v in sorted(answers.items()))
        for e in [
            AgentEvent(kind="message", text=f"답변({summary})을 반영했습니다."),
            AgentEvent(kind="stage", payload=json.dumps(
                {"stage": "Envision", "status": "completed", "summary": "답변 반영"},
                ensure_ascii=False)),
            AgentEvent(kind="document", payload=json.dumps(
                {"path": "aiplc-docs/discovery/discovery-document.md",
                 "version": "v1", "summary": "초안 생성"}, ensure_ascii=False)),
            AgentEvent(kind="done"),
        ]:
            yield e

    async def pending(self):
        return self._pending_payload

    async def interrupt(self):
        self.interrupts += 1

    async def stop(self):
        pass


def _watch(pid: str, turn_id: str, after: int = 0) -> list[str]:
    """턴 하나의 SSE 줄 전부 — 턴이 끝날 때까지 본다."""
    with client.stream("GET", f"/projects/{pid}/events",
                       params={"turn": turn_id, "after": after}) as r:
        assert r.status_code == 200, r.read()
        return list(r.iter_lines())


def _say(pid: str, text: str) -> list[str]:
    """메시지 턴을 시작하고 끝까지 본다."""
    r = client.post(f"/projects/{pid}/turns", json={"text": text})
    assert r.status_code == 200, r.text
    return _watch(pid, r.json()["turn_id"])


def _answer(pid: str, answers: dict) -> list[str]:
    """답변 턴을 시작하고 끝까지 본다."""
    r = client.post(f"/projects/{pid}/answers", json={"answers": answers})
    assert r.status_code == 200, r.text
    return _watch(pid, r.json()["turn_id"])


def _install_scripted(monkeypatch, pid, script):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")  # offline: no durable manifest write

    async def make(project_id):
        return Workspace(ScriptRunner(script))

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})


def _install_default(monkeypatch, pid):
    """Install a ScriptRunner with its default structured-demo script, so
    send_message arms a pending interrupt that send_answers/pending can then
    be exercised against.

    Returns the runner so a test can assert on what the route did to it
    (the interrupt route has no response body to check).
    """
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    runner = ScriptRunner()

    async def make(project_id):
        return Workspace(runner)

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    return runner


def test_sse_stream_emits_frames(monkeypatch):
    def script(text):
        return [AgentEvent(kind="status", text="working"),
                AgentEvent(kind="message", text="ok"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn2", script)
    body = "\n".join(_say("turn2", "go"))
    assert "working" in body
    assert "ok" in body
    assert '"kind":"done"' in body.replace(" ", "")

def test_sse_redacts_credentials_in_event_text(monkeypatch):
    def script(text):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred2", script)
    body = "\n".join(_say("turnred2", "go"))
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body

def test_answers_stream_relays_events(monkeypatch):
    _install_default(monkeypatch, "turnans1")
    # arm the pending interrupt via the default structured-demo script
    _say("turnans1", "시작")
    lines = [l for l in _answer("turnans1", {"1": "A", "2": "B"})
             if l.startswith("data:")]
    kinds = [json.loads(l[len("data:"):].strip())["kind"] for l in lines]
    assert "document" in kinds and kinds[-1] == "done"

def test_pending_endpoint(monkeypatch):
    _install_default(monkeypatch, "turnpend1")
    assert client.get("/projects/turnpend1/pending").json() == {"pending": None}
    _say("turnpend1", "시작")
    body = client.get("/projects/turnpend1/pending").json()
    assert body["pending"] is not None

def test_pending_unknown_project_404():
    r = client.get("/projects/does-not-exist/pending")
    assert r.status_code == 404

def test_payload_is_redacted(monkeypatch):
    """questions payload with a credential-looking string is redacted at the
    route seam, same as text."""
    leak = json.dumps({"interrupt_id": "i", "questions": {
        "note": "key AKIAIOSFODNN7EXAMPLE here"}})
    def script(text):
        return [AgentEvent(kind="questions", payload=leak), AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnredpayload", script)
    lines = [l for l in _say("turnredpayload", "hi") if l.startswith("data:")]
    body = "".join(lines)
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body


# ---- 턴 중단 ----

def test_interrupt_reaches_the_runner(monkeypatch):
    """라우트가 실제로 러너까지 도달하는가. 202만 돌려주고 아무것도 하지 않는
    라우트는 화면에서 구별되지 않는다 — 버튼은 눌리고 턴은 계속 돈다."""
    runner = _install_default(monkeypatch, "int-1")
    r = client.post("/projects/int-1/interrupt")
    assert r.status_code == 202
    assert runner.interrupts == 1


def test_interrupt_is_idempotent(monkeypatch):
    """두 번 눌러도 같다. 사용자가 반응이 없다고 다시 누르는 것이 정상 경로이고,
    돌고 있는 턴이 없을 때도 에러가 아니다."""
    runner = _install_default(monkeypatch, "int-2")
    assert client.post("/projects/int-2/interrupt").status_code == 202
    assert client.post("/projects/int-2/interrupt").status_code == 202
    assert runner.interrupts == 2


def test_interrupt_on_an_unknown_project_is_404(monkeypatch):
    """ensure_workspace의 기존 계약 — 없는 프로젝트는 404다. 중단이 멱등인 것과
    별개다(있는 프로젝트의 없는 턴 ≠ 없는 프로젝트)."""
    _install_default(monkeypatch, "int-3")
    assert client.post("/projects/nope/interrupt").status_code == 404


def test_interrupt_survives_a_raising_runner(monkeypatch):
    """드라이버의 client.interrupt()가 던져도 라우트의 docstring이 약속하는
    202/멱등 계약은 지킨다 — 사용자는 중단이 안 먹혔다고 다시 누를 뿐, 500을
    받을 이유가 없다."""
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    runner = ScriptRunner()

    async def boom():
        raise RuntimeError("subprocess pipe closed")
    runner.interrupt = boom

    async def make(project_id):
        return Workspace(runner)
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": "int-4"})

    assert client.post("/projects/int-4/interrupt").status_code == 202


# ---- 긴 입력을 URL에서 빼는 2단계 핸들 (HTTP 431 결함) ----
#
# 실측한 결함: 한글 2,164자 입력이 encodeURIComponent로 14,376바이트 요청
# 라인이 되고, 인증 쿠키(JWT 3개 ~3.7KB)와 합쳐 Node의 maxHeaderSize
# 16,384바이트를 넘겨 프록시가 431을 냈다. EventSource는 상태 코드를 노출하지
# 않아 화면에는 "연결이 끊어졌습니다"만 떴다.

_LONG_KO = "가" * 3000


def test_turn_handle_carries_text_out_of_the_url(monkeypatch):
    """POST로 텍스트를 받고, SSE는 짧은 핸들만 URL에 싣는다."""
    seen = {}

    def script(text):
        seen["text"] = text
        return [AgentEvent(kind="message", text="ok"), AgentEvent(kind="done")]

    _install_scripted(monkeypatch, "turnh1", script)
    r = client.post("/projects/turnh1/turns", json={"text": _LONG_KO})
    assert r.status_code == 200
    handle = r.json()["turn_id"]
    # 이것이 이 설계의 핵심 단정 — 핸들이 URL에 들어가므로 짧아야 한다.
    assert len(handle) <= 64
    with client.stream("GET", "/projects/turnh1/events",
                       params={"turn": handle}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    assert "ok" in body
    # 에이전트는 원문 전체를 받았다 — 핸들이 텍스트를 잘라먹지 않는다.
    assert seen["text"] == _LONG_KO


def _data(lines):
    return [json.loads(l[len("data:"):].strip()) for l in lines
            if l.startswith("data:")]


def test_a_turn_can_be_watched_again_from_any_seq(monkeypatch):
    """턴 id는 1회용 핸들이 아니라 **다시 보는** 이름이다.

    새로고침·절전 복귀·두 번째 탭이 모두 같은 턴을 처음부터(`after=0`) 또는 마지막으로
    받은 곳부터(`after=n`) 본다.
    """
    def script(text):
        return [AgentEvent(kind="message", text="하나"),
                AgentEvent(kind="message", text="둘"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnh2", script)
    turn = client.post("/projects/turnh2/turns",
                       json={"text": "go"}).json()["turn_id"]
    with client.stream("GET", "/projects/turnh2/events",
                       params={"turn": turn}) as r:
        first = list(r.iter_lines())
    with client.stream("GET", "/projects/turnh2/events",
                       params={"turn": turn}) as r:
        again = list(r.iter_lines())
    assert _data(first) == _data(again)
    assert [e["text"] for e in _data(first)[:2]] == ["하나", "둘"]
    # 프레임의 id가 seq다 — 다시 붙을 때 `after`로 돌려준다.
    assert "id: 1" in first
    with client.stream("GET", "/projects/turnh2/events",
                       params={"turn": turn, "after": 1}) as r:
        tail = _data(list(r.iter_lines()))
    assert [e.get("text") for e in tail] == ["둘", None]
    assert tail[-1]["kind"] == "done"


def test_an_unknown_turn_is_404(monkeypatch):
    _install_default(monkeypatch, "turnh3")
    r = client.get("/projects/turnh3/events", params={"turn": "deadbeef"})
    assert r.status_code == 404


def test_events_only_watches_a_turn(monkeypatch):
    """`/events`는 턴을 **보기만** 한다 — 턴은 POST가 시작한다. URL에 입력을 실어
    시작하는 길이 없으므로 긴 입력이 요청 라인에 실릴 일도 없다(431)."""
    _install_default(monkeypatch, "turnh4")
    assert client.get("/projects/turnh4/events").status_code == 422
    assert client.get("/projects/turnh4/events",
                      params={"text": "go"}).status_code == 422


def test_a_turn_is_scoped_to_its_project(monkeypatch):
    _install_default(monkeypatch, "turnh6")
    _install_default(monkeypatch, "turnh7")
    turn = client.post("/projects/turnh6/turns",
                       json={"text": "p6의 입력"}).json()["turn_id"]
    # 다른 프로젝트에서 같은 id로 볼 수 없다.
    r = client.get("/projects/turnh7/events", params={"turn": turn})
    assert r.status_code == 404


def test_turns_unknown_project_404():
    r = client.post("/projects/does-not-exist/turns", json={"text": "hi"})
    assert r.status_code == 404


def test_answers_handle_carries_answers_out_of_the_url(monkeypatch):
    """답변 제출도 같은 배관을 쓴다 — 자유 서술이 길면 같은 한도에 걸린다."""
    _install_default(monkeypatch, "turnh8")
    _say("turnh8", "시작")
    long_answers = {"1": "A", "2": "긴 자유 서술 " * 400}
    r = client.post("/projects/turnh8/answers", json={"answers": long_answers})
    assert r.status_code == 200
    handle = r.json()["turn_id"]
    assert len(handle) <= 64
    lines = [l for l in _watch("turnh8", handle) if l.startswith("data:")]
    kinds = [json.loads(l[len("data:"):].strip())["kind"] for l in lines]
    assert kinds[-1] == "done"


# ---- 턴은 서버 작업이다 ----
#
# 턴의 수명은 요청의 것이 아니다(aipds/turn_job.py 헤더). 아래 테스트는 러너가
# `gate`에서 멈추는 턴으로 "도는 중"을 만든다.

class GatedRunner(ScriptRunner):
    """첫 메시지를 낸 뒤 `gate`가 열릴 때까지 멈추는 턴. 끝까지 돌았는지 기록한다."""

    def __init__(self):
        super().__init__()
        self.gate = None
        self.finished = False
        self.sent = []

    async def send_message(self, text):
        self.sent.append(text)
        self.gate = self.gate or asyncio.Event()
        try:
            yield AgentEvent(kind="message", text="첫 문장")
            await self.gate.wait()
            yield AgentEvent(kind="message", text="자리를 비운 동안 온 문장")
            yield AgentEvent(kind="done")
        finally:
            # 러너의 종결 sync가 도는 자리 — 여기까지 와야 산출물이 S3에 오른다.
            self.finished = True


def _install_gated(monkeypatch, pid) -> GatedRunner:
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    runner = GatedRunner()

    async def make(project_id):
        return Workspace(runner)

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    return runner


def _turn_state(pid):
    return client.get(f"/projects/{pid}/turn").json()["turn"]


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _open_gate(runner, delay: float = 0.0):
    """턴을 이어 가게 한다. `delay`가 있으면 그만큼 뒤에 연다.

    TestClient는 스트림 요청도 앱이 응답을 끝낼 때까지 붙잡으므로, 스트림을 여는
    쪽에서 문을 열 수 없다 — 미리 예약해 둔다. 보던 화면이 **도중에** 떠나는 경우는
    같은 이유로 여기서 만들 수 없어 tests/test_turn_job.py가 맡는다.
    """
    client.portal.start_task_soon(_set, runner, delay)


async def _set(runner, delay):
    await asyncio.sleep(delay)
    runner.gate.set()


def test_a_second_turn_while_one_runs_is_409_with_the_running_id(monkeypatch):
    """거절된 쪽이 할 일은 다시 보내기가 아니라 도는 턴을 보는 것이다."""
    runner = _install_gated(monkeypatch, "own2")
    turn = client.post("/projects/own2/turns", json={"text": "go"}).json()["turn_id"]
    r = client.post("/projects/own2/turns", json={"text": "또"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "turn_in_progress", "turn_id": turn}
    assert runner.sent == ["go"]    # 두 번째 입력은 에이전트에 닿지 않았다
    _open_gate(runner)
    _wait_for(lambda: _turn_state("own2")["state"] == "done")
    # 끝난 뒤에는 다시 받는다.
    assert client.post("/projects/own2/turns", json={"text": "또"}).status_code == 200


def test_turn_reports_the_running_turn_then_its_end(monkeypatch):
    runner = _install_gated(monkeypatch, "own3")
    assert _turn_state("own3") is None
    turn = client.post("/projects/own3/turns", json={"text": "go"}).json()["turn_id"]
    _wait_for(lambda: _turn_state("own3")["last_seq"] >= 1)
    state = _turn_state("own3")
    assert state["turn_id"] == turn and state["state"] == "running"
    _open_gate(runner)
    _wait_for(lambda: _turn_state("own3")["state"] == "done")


def test_turn_reports_a_turn_the_restart_cut_off(monkeypatch):
    """메모리에 턴이 없는데 표식이 `running`이면 재시작으로 끊긴 턴이다."""
    _install_default(monkeypatch, "own4")
    s3 = FakeS3Store()
    s3.blobs[TURN_MARKER_KEY] = json.dumps(
        {"turn_id": "t-before", "kind": "message", "state": "running"})
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bucket")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    state = _turn_state("own4")
    assert state == {"turn_id": "t-before", "kind": "message",
                     "state": "interrupted", "last_seq": 0}


def test_a_finished_marker_is_not_reported_as_interrupted(monkeypatch):
    _install_default(monkeypatch, "own5")
    s3 = FakeS3Store()
    s3.blobs[TURN_MARKER_KEY] = json.dumps(
        {"turn_id": "t-before", "kind": "message", "state": "done"})
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bucket")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    assert _turn_state("own5") is None


