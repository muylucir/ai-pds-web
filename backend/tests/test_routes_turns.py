# backend/tests/test_routes_turns.py
import json
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.workspace import Workspace
from aipds.models import AgentEvent

client = TestClient(app_module.app)

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
        self.input_holder = None
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

    #: `GET /events/live`가 부르는 것. 기본은 "붙을 턴이 없음"이고, 테스트가
    #: `live_script`를 채우면 그것을 흘린다.
    live_script = None

    async def reattach(self):
        for e in (self.live_script or [AgentEvent(kind="done")]):
            yield e

    async def interrupt(self):
        self.interrupts += 1

    async def stop(self):
        pass


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


def test_message_returns_events(monkeypatch):
    def script(text):
        return [AgentEvent(kind="message", text=f"got {text}"), AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn1", script)
    r = client.post("/projects/turn1/message", json={"text": "승인"})
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds == ["message", "done"]
    assert "승인" in r.json()["events"][0]["text"]

def test_sse_stream_emits_frames(monkeypatch):
    def script(text):
        return [AgentEvent(kind="status", text="working"),
                AgentEvent(kind="message", text="ok"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn2", script)
    with client.stream("GET", "/projects/turn2/events", params={"text": "go"}) as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert "working" in body
    assert "ok" in body
    assert '"kind":"done"' in body.replace(" ", "")

def test_message_redacts_credentials_in_event_text(monkeypatch):
    def script(text):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred1", script)
    r = client.post("/projects/turnred1/message", json={"text": "go"})
    assert r.status_code == 200
    joined = " ".join(e.get("text") or "" for e in r.json()["events"])
    assert "AKIA" not in joined
    assert "[CREDENTIAL REDACTED]" in joined

def test_sse_redacts_credentials_in_event_text(monkeypatch):
    def script(text):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred2", script)
    with client.stream("GET", "/projects/turnred2/events", params={"text": "go"}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body

def test_answers_stream_relays_events(monkeypatch):
    _install_default(monkeypatch, "turnans1")
    # arm the pending interrupt via the default structured-demo script
    with client.stream("GET", "/projects/turnans1/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    answers = json.dumps({"1": "A", "2": "B"})
    with client.stream("GET", "/projects/turnans1/answers/stream",
                       params={"answers": answers}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
    kinds = [json.loads(l[len("data:"):].strip())["kind"] for l in lines]
    assert "document" in kinds and kinds[-1] == "done"

def test_pending_endpoint(monkeypatch):
    _install_default(monkeypatch, "turnpend1")
    assert client.get("/projects/turnpend1/pending").json() == {"pending": None}
    with client.stream("GET", "/projects/turnpend1/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    body = client.get("/projects/turnpend1/pending").json()
    assert body["pending"] is not None

def test_answers_stream_bad_json_400(monkeypatch):
    _install_default(monkeypatch, "turnbad1")
    r = client.get("/projects/turnbad1/answers/stream", params={"answers": "not-json"})
    assert r.status_code == 400

def test_answers_stream_unknown_project_404():
    r = client.get("/projects/does-not-exist/answers/stream", params={"answers": "{}"})
    assert r.status_code == 404

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
    with client.stream("GET", "/projects/turnredpayload/events", params={"text": "hi"}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
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


def test_a_turn_handle_is_single_use(monkeypatch):
    _install_default(monkeypatch, "turnh2")
    handle = client.post("/projects/turnh2/turns",
                         json={"text": "한 번만"}).json()["turn_id"]
    with client.stream("GET", "/projects/turnh2/events",
                       params={"turn": handle}) as r:
        list(r.iter_lines())
    # URL에 남은 핸들로 같은 턴을 다시 돌릴 수 없다.
    again = client.get("/projects/turnh2/events", params={"turn": handle})
    assert again.status_code == 400


def test_an_unknown_turn_handle_is_400(monkeypatch):
    _install_default(monkeypatch, "turnh3")
    r = client.get("/projects/turnh3/events", params={"turn": "deadbeef"})
    assert r.status_code == 400


def test_events_requires_text_or_turn(monkeypatch):
    """둘 다 없으면 무엇을 보낼지 알 수 없다 — 조용히 빈 턴을 돌리지 않는다."""
    _install_default(monkeypatch, "turnh4")
    r = client.get("/projects/turnh4/events")
    assert r.status_code == 400


def test_text_query_param_still_works(monkeypatch):
    """짧은 입력의 기존 경로는 유지한다 — 프론트 배포와 백엔드 배포가 원자적이
    아니므로, 구 프론트가 보내는 ?text=가 계속 동작해야 한다."""
    _install_default(monkeypatch, "turnh5")
    with client.stream("GET", "/projects/turnh5/events",
                       params={"text": "짧은 입력"}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
    assert lines


def test_turns_handle_is_scoped_to_its_project(monkeypatch):
    _install_default(monkeypatch, "turnh6")
    _install_default(monkeypatch, "turnh7")
    handle = client.post("/projects/turnh6/turns",
                         json={"text": "p6의 입력"}).json()["turn_id"]
    # 다른 프로젝트에서 같은 핸들을 쓸 수 없다.
    r = client.get("/projects/turnh7/events", params={"turn": handle})
    assert r.status_code == 400


def test_turns_unknown_project_404():
    r = client.post("/projects/does-not-exist/turns", json={"text": "hi"})
    assert r.status_code == 404


def test_answers_handle_carries_answers_out_of_the_url(monkeypatch):
    """답변 제출도 같은 배관을 쓴다 — 자유 서술이 길면 같은 한도에 걸린다."""
    _install_default(monkeypatch, "turnh8")
    with client.stream("GET", "/projects/turnh8/events",
                       params={"text": "시작"}) as r:
        list(r.iter_lines())
    long_answers = {"1": "A", "2": "긴 자유 서술 " * 400}
    r = client.post("/projects/turnh8/answers", json={"answers": long_answers})
    assert r.status_code == 200
    handle = r.json()["turn_id"]
    assert len(handle) <= 64
    with client.stream("GET", "/projects/turnh8/answers/stream",
                       params={"turn": handle}) as resp:
        lines = [l for l in resp.iter_lines() if l.startswith("data:")]
    kinds = [json.loads(l[len("data:"):].strip())["kind"] for l in lines]
    assert kinds[-1] == "done"


def test_answers_stream_still_accepts_the_answers_query_param(monkeypatch):
    _install_default(monkeypatch, "turnh9")
    with client.stream("GET", "/projects/turnh9/events",
                       params={"text": "시작"}) as r:
        list(r.iter_lines())
    with client.stream("GET", "/projects/turnh9/answers/stream",
                       params={"answers": json.dumps({"1": "A"})}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
    assert lines


def test_answers_stream_requires_answers_or_turn(monkeypatch):
    _install_default(monkeypatch, "turnh10")
    r = client.get("/projects/turnh10/answers/stream")
    assert r.status_code == 400


def test_live_stream_relays_an_in_flight_turn(monkeypatch):
    """절전에서 돌아온 브라우저가 붙는 경로. **핸들이 없다** — 다른 스트림들은
    POST가 만든 1회용·60초 핸들을 요구해서 재접속에 쓸 수 없다."""
    # 러너는 요청마다 새로 만들어지므로(_install_scripted의 make) 클래스 속성에
    # 심는다. monkeypatch가 테스트 뒤 되돌린다.
    monkeypatch.setattr(ScriptRunner, "live_script",
                        [AgentEvent(kind="message", text="자리를 비운 동안 온 문장"),
                         AgentEvent(kind="done")])
    _install_scripted(monkeypatch, "live1", _structured_first_turn)
    with client.stream("GET", "/projects/live1/events/live") as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "자리를 비운 동안 온 문장" in body
    assert '"kind":"done"' in body.replace(" ", "")


def test_live_stream_ends_quietly_when_nothing_is_running(monkeypatch):
    """늦게 돌아와 턴이 끝난 경우 — 에러가 아니라 `done`이어야 프론트가
    `GET /history`로 복원한다."""
    _install_scripted(monkeypatch, "live2", _structured_first_turn)
    with client.stream("GET", "/projects/live2/events/live") as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert '"kind":"error"' not in body.replace(" ", "")
    assert '"kind":"done"' in body.replace(" ", "")
