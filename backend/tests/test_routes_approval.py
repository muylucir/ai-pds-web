# 승인 라우트의 계약.
#
# 이 라우트가 존재하는 이유는 approval_store.py 헤더에 있다: 승인 판정의 근거를
# 에이전트의 산문에서 **우리가 쓰는 레코드**로 옮긴다.
#
# 이 파일이 지키는 가장 중요한 불변식은 **순서**다. 레코드를 먼저 쓰고 그 다음
# 에이전트 턴을 시작한다. 승인의 기록이 에이전트의 산문뿐이면 턴이 200이어도
# 에이전트가 문구를 달리 쓸 때 승인이 사라진다 — 사용자는 버튼을 눌렀는데 게이트가
# 그대로다.
import asyncio

from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.app import app, registry
from aipds.approval_store import load_approvals
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app)

_DOC = "aiplc-docs/discovery/discovery-document.md"


def _seed(monkeypatch, pid, *, doc_text: str | None = "# Discovery Document\n본문\n"):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    s3 = FakeS3Store()
    monkeypatch.setattr(app_module, "s3_store_factory", lambda project_id: s3)

    async def make(project_id):
        return Workspace(FakeRunner())

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    ws = registry.get(pid)
    if doc_text is not None:
        asyncio.run(ws.runner.write_file(_DOC, doc_text))
    return s3


def test_approve_records_the_decision(monkeypatch):
    s3 = _seed(monkeypatch, "ap1")
    r = client.post("/projects/ap1/approve")
    assert r.status_code == 200

    records = asyncio.run(load_approvals(s3))
    assert len(records) == 1
    assert records[0].document == _DOC
    assert records[0].doc_hash  # 무효화 판정에 쓰인다 — 비어 있으면 의미가 없다


def test_approve_records_before_starting_the_agent_turn(monkeypatch):
    """레코드가 먼저다 — 이 순서가 이 기능의 핵심이다.

    턴이 실패해도 승인 사실은 남아야 한다. 승인의 기록이 에이전트가 쓰는
    audit.md뿐이면, 턴이 실패하거나 에이전트가 문구를 달리 쓸 때 사용자가 누른
    사실 자체가 사라진다.
    """
    s3 = _seed(monkeypatch, "ap2")
    ws = registry.get("ap2")
    seen_at_start = []

    def send_message(text):
        # 턴이 **시작되는** 순간 레코드가 이미 있어야 한다.
        seen_at_start.append(any(k.startswith("approvals/") for k in s3.blobs))

        async def events():
            raise RuntimeError("agent turn blew up")
            yield  # pragma: no cover — async generator로 만들기 위해
        return events()

    monkeypatch.setattr(ws.runner, "send_message", send_message)

    assert client.post("/projects/ap2/approve").status_code == 200
    assert seen_at_start == [True], "턴 이전에 레코드가 있어야 한다"
    # 턴이 터졌어도 승인은 남는다.
    assert len(asyncio.run(load_approvals(s3))) == 1


def test_approve_starts_the_approval_turn_and_returns_its_id(monkeypatch):
    # 레코드만 쓰고 끝내면 에이전트가 다음 단계로 진행하지 않는다 — 게이트의
    # 목적은 기록이 아니라 워크플로 진행이다. audit.md 기록도 그 턴이 만든다.
    _seed(monkeypatch, "ap3")
    body = client.post("/projects/ap3/approve").json()
    assert registry.get("ap3").runner.sent == ["승인"]
    # 화면은 이 id로 턴을 본다 — 승인 요청은 턴을 기다리지 않는다.
    assert body["approved"] is True and body["turn_id"]


def test_approve_does_not_wait_for_the_turn(monkeypatch):
    """승인 응답은 턴이 끝나기 전에 온다.

    다음 단계로 넘어가는 턴은 몇 분이고, 요청이 그동안 열려 있으면 프록시
    타임아웃(CloudFront 60초)이 승인 응답을 504로 바꾼다.
    """
    _seed(monkeypatch, "ap11")
    ws = registry.get("ap11")

    def send_message(text):
        async def events():
            await asyncio.Event().wait()     # 영원히 끝나지 않는 턴
            yield  # pragma: no cover
        return events()

    monkeypatch.setattr(ws.runner, "send_message", send_message)
    with TestClient(app) as live:
        r = live.post("/projects/ap11/approve")
        assert r.status_code == 200
        turn = live.get("/projects/ap11/turn").json()["turn"]
        assert turn["turn_id"] == r.json()["turn_id"]
        assert turn["state"] == "running"
        live.portal.call(ws.turns.cancel)


def test_approve_while_a_turn_is_running_is_409_and_records_nothing(monkeypatch):
    """에이전트가 문서를 고치는 중에 받은 승인은 곧 해시가 어긋난다.

    레코드만 남기면 사용자는 "승인됨"을 보는데 에이전트는 그 사실을 전달받지
    못한다 — 그래서 레코드 **전에** 거절하고, 도는 턴의 id를 알려 준다.
    """
    s3 = _seed(monkeypatch, "ap12")
    ws = registry.get("ap12")

    def send_message(text):
        async def events():
            await asyncio.Event().wait()
            yield  # pragma: no cover
        return events()

    monkeypatch.setattr(ws.runner, "send_message", send_message)
    with TestClient(app) as live:
        running = live.post("/projects/ap12/turns", json={"text": "go"}).json()
        r = live.post("/projects/ap12/approve")
        assert r.status_code == 409
        assert r.json()["detail"] == {"code": "turn_in_progress",
                                      "turn_id": running["turn_id"]}
        assert not any(k.startswith("approvals/") for k in s3.blobs)
        live.portal.call(ws.turns.cancel)


def test_approvals_are_listable(monkeypatch):
    _seed(monkeypatch, "ap4")
    client.post("/projects/ap4/approve")

    r = client.get("/projects/ap4/approvals")
    assert r.status_code == 200
    body = r.json()["approvals"]
    assert len(body) == 1
    assert body[0]["document"] == _DOC
    assert "doc_hash" in body[0] and "approved_at" in body[0]


def test_approvals_reports_the_current_document_hash(monkeypatch):
    """현재 문서 해시를 **백엔드가** 함께 준다.

    프론트가 스스로 해시를 계산하면 알고리즘이 두 곳에 생기고, 둘이 어긋나는
    순간 승인이 영구히 인식되지 않는다(그 실패는 조용하다 — 게이트가 안 열릴
    뿐이다). 해시의 정의는 승인을 쓰는 쪽이 소유해야 한다.
    """
    _seed(monkeypatch, "ap8")
    client.post("/projects/ap8/approve")

    body = client.get("/projects/ap8/approvals").json()
    # 방금 승인했으므로 현재 해시가 승인된 해시와 같아야 한다.
    assert body["current_doc_hash"] == body["approvals"][0]["doc_hash"]


def test_current_doc_hash_changes_when_the_document_is_edited(monkeypatch):
    # 이 값이 승인 해시와 달라지는 것이 "재승인 필요"의 근거다.
    _seed(monkeypatch, "ap9", doc_text="첫 버전\n")
    client.post("/projects/ap9/approve")
    approved = client.get("/projects/ap9/approvals").json()["approvals"][0]["doc_hash"]

    asyncio.run(registry.get("ap9").runner.write_file(_DOC, "고친 버전\n"))
    assert client.get("/projects/ap9/approvals").json()["current_doc_hash"] != approved


def test_current_doc_hash_is_null_when_there_is_no_document(monkeypatch):
    # 문서가 없으면 비교할 것이 없다. 빈 문자열을 주면 프론트가 "해시가 있다"고
    # 오해해 승인 여부를 잘못 판정할 수 있다.
    _seed(monkeypatch, "ap10", doc_text=None)
    assert client.get("/projects/ap10/approvals").json()["current_doc_hash"] is None


def test_approvals_is_empty_for_a_project_that_never_approved(monkeypatch):
    # 이 기능 이전의 모든 프로젝트가 이 상태다 — 404가 아니라 빈 목록이어야
    # 하고, 그때는 프론트가 감사 로그 폴백으로 판정한다.
    _seed(monkeypatch, "ap5")
    r = client.get("/projects/ap5/approvals")
    assert r.status_code == 200
    assert r.json()["approvals"] == []


def test_the_hash_tracks_the_document_text(monkeypatch):
    # 문서가 바뀌면 해시가 달라져야 한다 — 그것이 "승인 후 문서가 바뀌면 다시
    # 미승인"을 추측(산문에서 '수정' 찾기)이 아니라 사실로 만드는 근거다.
    s3 = _seed(monkeypatch, "ap6", doc_text="첫 버전\n")
    client.post("/projects/ap6/approve")
    first = asyncio.run(load_approvals(s3))[0].doc_hash

    ws = registry.get("ap6")
    asyncio.run(ws.runner.write_file(_DOC, "고친 버전\n"))
    client.post("/projects/ap6/approve")
    second = asyncio.run(load_approvals(s3))[1].doc_hash

    assert first != second


def test_approve_on_an_unknown_project_is_404(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    assert client.post("/projects/nope-not-here/approve").status_code == 404


def test_approve_refuses_when_the_document_does_not_exist(monkeypatch):
    """승인할 문서가 없으면 거부한다.

    빈 해시로 레코드를 쓰면 무효화 판정이 영구히 무의미해진다(무엇과 비교해도
    같지 않다). 게이트는 문서를 보고 있을 때만 뜨므로 정상 경로로는 오지
    않지만, 조용히 통과시키면 그 사실을 아무도 모른다.
    """
    _seed(monkeypatch, "ap7", doc_text=None)
    assert client.post("/projects/ap7/approve").status_code == 409
