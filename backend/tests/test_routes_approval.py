# 승인 라우트의 계약.
#
# 이 라우트가 존재하는 이유는 approval_store.py 헤더에 있다: 승인 판정의 근거를
# 에이전트의 산문에서 **우리가 쓰는 레코드**로 옮긴다.
#
# 이 파일이 지키는 가장 중요한 불변식은 **순서**다. 레코드를 먼저 쓰고 그 다음
# 에이전트 턴을 돌린다. 종전 구조에서는 턴이 200이어도 에이전트가 문구를 달리
# 쓰면 승인이 사라졌다 — 사용자는 버튼을 눌렀는데 게이트가 그대로였다.
import asyncio

from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.app import app, registry
from pathfinder.approval_store import load_approvals
from pathfinder.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app)

_DOC = "aiplc-docs/discovery/discovery-document.md"


def _seed(monkeypatch, pid, *, doc_text: str | None = "# Discovery Document\n본문\n"):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
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


def test_approve_records_before_running_the_agent_turn(monkeypatch):
    """레코드가 먼저다 — 이 순서가 이 기능의 핵심이다.

    턴이 실패해도 승인 사실은 남아야 한다. 종전에는 승인의 유일한 기록이
    에이전트가 쓰는 audit.md였으므로, 턴이 실패하거나 에이전트가 문구를 달리
    쓰면 사용자가 누른 사실 자체가 사라졌다.
    """
    s3 = _seed(monkeypatch, "ap2")
    ws = registry.get("ap2")

    async def failing_send(text):
        # 레코드가 이미 저장돼 있어야 한다 — 이 시점에 조회해서 확인한다.
        assert len(await load_approvals(s3)) == 1, "턴 이전에 레코드가 있어야 한다"
        raise RuntimeError("agent turn blew up")
        yield  # pragma: no cover — async generator로 만들기 위해

    monkeypatch.setattr(ws.runner, "send_message", failing_send, raising=False)

    client.post("/projects/ap2/approve")

    # 턴이 터졌어도 승인은 남는다.
    assert len(asyncio.run(load_approvals(s3))) == 1


def test_approve_still_sends_the_approval_turn(monkeypatch):
    # 레코드만 쓰고 끝내면 에이전트가 다음 단계로 진행하지 않는다 — 게이트의
    # 목적은 기록이 아니라 워크플로 진행이다. audit.md 기록도 그 턴이 만든다.
    _seed(monkeypatch, "ap3")
    ws = registry.get("ap3")
    sent = []

    # FakeRunner에는 send_message가 없다(파일 IO만 흉내낸다) — 실제 러너의
    # 그 메서드를 여기서 주입한다. raising=False가 필요한 이유가 그것이다.
    async def spy(text):
        sent.append(text)
        return
        yield  # pragma: no cover — async generator로 만들기 위해

    monkeypatch.setattr(ws.runner, "send_message", spy, raising=False)

    assert client.post("/projects/ap3/approve").status_code == 200
    assert sent, "승인 턴이 에이전트에게 전달되어야 한다"


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
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    assert client.post("/projects/nope-not-here/approve").status_code == 404


def test_approve_refuses_when_the_document_does_not_exist(monkeypatch):
    """승인할 문서가 없으면 거부한다.

    빈 해시로 레코드를 쓰면 무효화 판정이 영구히 무의미해진다(무엇과 비교해도
    같지 않다). 게이트는 문서를 보고 있을 때만 뜨므로 정상 경로로는 오지
    않지만, 조용히 통과시키면 그 사실을 아무도 모른다.
    """
    _seed(monkeypatch, "ap7", doc_text=None)
    assert client.post("/projects/ap7/approve").status_code == 409
