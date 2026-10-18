# Contract of the approval route.
#
# Why this route exists is in the approval_store.py header: it moves the basis for
# judging approval from the agent's prose to **a record we write**.
#
# The most important invariant this file guards is **order**. Write the record first,
# then start the agent turn. If the only record of approval is the agent's prose, then
# even when the turn returns 200, the approval vanishes when the agent words it
# differently: the user pressed the button, yet the gate stays as it was.
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
    assert records[0].doc_hash  # used to decide invalidation; meaningless if empty


def test_approve_records_before_starting_the_agent_turn(monkeypatch):
    """The record comes first; this order is the core of the feature.

    The fact of approval must survive even if the turn fails. If the only record of
    approval were the audit.md the agent writes, then when the turn fails or the agent
    words it differently, the very fact that the user pressed the button would be lost.
    """
    s3 = _seed(monkeypatch, "ap2")
    ws = registry.get("ap2")
    seen_at_start = []

    def send_message(text, record=None):
        # The record must already exist at the moment the turn **starts**.
        seen_at_start.append(any(k.startswith("approvals/") for k in s3.blobs))

        async def events():
            raise RuntimeError("agent turn blew up")
            yield  # pragma: no cover — to make this an async generator
        return events()

    monkeypatch.setattr(ws.runner, "send_message", send_message)

    assert client.post("/projects/ap2/approve").status_code == 200
    assert seen_at_start == [True], "턴 이전에 레코드가 있어야 한다"
    # The approval survives even though the turn blew up.
    assert len(asyncio.run(load_approvals(s3))) == 1


def test_approve_starts_the_approval_turn_and_returns_its_id(monkeypatch):
    # Writing only the record and stopping would leave the agent not moving to the next
    # stage; the gate exists to advance the workflow, not to record. That turn also writes audit.md.
    _seed(monkeypatch, "ap3")
    body = client.post("/projects/ap3/approve").json()
    assert registry.get("ap3").runner.sent == ["승인"]
    # The web side records it in the audit log as "문서 승인" (document approval) (aipds/audit_log).
    record = registry.get("ap3").runner.records[-1]
    assert (record.source, record.text) == ("approval", "승인")
    # The screen follows the turn by this id; the approval request does not wait for the turn.
    assert body["approved"] is True and body["turn_id"]


def test_approve_does_not_wait_for_the_turn(monkeypatch):
    """The approval response arrives before the turn ends.

    A turn that advances to the next stage takes minutes, and if the request stayed open
    that long, the proxy timeout (CloudFront 60 seconds) would turn the approval response into a 504.
    """
    _seed(monkeypatch, "ap11")
    ws = registry.get("ap11")

    def send_message(text, record=None):
        async def events():
            await asyncio.Event().wait()     # a turn that never ends
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
    """An approval received while the agent is editing the document soon has a mismatched hash.

    If only the record were kept, the user would see "approved" while the agent never
    learns of it, so the request is rejected **before** the record, returning the id of the running turn.
    """
    s3 = _seed(monkeypatch, "ap12")
    ws = registry.get("ap12")

    def send_message(text, record=None):
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
    """**The backend** supplies the current document hash alongside.

    If the frontend computed the hash itself, the algorithm would exist in two places, and
    the moment they diverged, approvals would never be recognized again (and that failure
    is silent: the gate just does not open). The side that writes approvals must own the
    definition of the hash.
    """
    _seed(monkeypatch, "ap8")
    client.post("/projects/ap8/approve")

    body = client.get("/projects/ap8/approvals").json()
    # Just approved, so the current hash must equal the approved hash.
    assert body["current_doc_hash"] == body["approvals"][0]["doc_hash"]


def test_current_doc_hash_changes_when_the_document_is_edited(monkeypatch):
    # This value diverging from the approved hash is the basis for "re-approval needed".
    _seed(monkeypatch, "ap9", doc_text="첫 버전\n")
    client.post("/projects/ap9/approve")
    approved = client.get("/projects/ap9/approvals").json()["approvals"][0]["doc_hash"]

    asyncio.run(registry.get("ap9").runner.write_file(_DOC, "고친 버전\n"))
    assert client.get("/projects/ap9/approvals").json()["current_doc_hash"] != approved


def test_current_doc_hash_is_null_when_there_is_no_document(monkeypatch):
    # With no document there is nothing to compare. Returning an empty string could make
    # the frontend think "there is a hash" and misjudge the approval state.
    _seed(monkeypatch, "ap10", doc_text=None)
    assert client.get("/projects/ap10/approvals").json()["current_doc_hash"] is None


def test_approvals_is_empty_for_a_project_that_never_approved(monkeypatch):
    # Every project from before this feature is in this state: it must be an empty list,
    # not 404, and in that case the frontend decides using the audit log fallback.
    _seed(monkeypatch, "ap5")
    r = client.get("/projects/ap5/approvals")
    assert r.status_code == 200
    assert r.json()["approvals"] == []


def test_the_hash_tracks_the_document_text(monkeypatch):
    # The hash must change when the document changes; that is what makes "if the document
    # changes after approval, it is unapproved again" a fact rather than a guess (looking
    # for '수정' (edit) in prose).
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
    """Reject when there is no document to approve.

    Writing a record with an empty hash makes the invalidation check permanently
    meaningless (it equals nothing it is compared with). The gate appears only while a
    document is being viewed, so the normal path never gets here, but letting it pass
    silently would mean nobody ever finds out.
    """
    _seed(monkeypatch, "ap7", doc_text=None)
    assert client.post("/projects/ap7/approve").status_code == 409
