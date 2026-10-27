# Documents as they stood when answers were handed over (aipds/doc_revisions.py).
#
# The point of the feature is the comparison the user sees after a question round, so the invariants
# guarded here are about **which text is the baseline**: the copy is taken before the agent's turn,
# only documents are in it, and a baseline equal to the current text is never offered.
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds import doc_revisions
from aipds.app import app, registry
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

_DOC = "aiplc-docs/discovery/envision/pain-points.md"
_SPEC = "aiplc-docs/discovery/prototype/prototype-spec.md"
_QFILE = "aiplc-docs/strategy-questions.md"


def _seed(monkeypatch, pid):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")

    async def make(project_id):
        return Workspace(FakeRunner())

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    runner = registry.get(pid).runner
    asyncio.run(runner.write_file(
        _QFILE, (FIX / "strategy-questions.md").read_text(encoding="utf-8")))
    return runner


def _write(runner, path, text):
    asyncio.run(runner.write_file(path, text))


def _submit(pid):
    r = client.post(f"/projects/{pid}/questions/{_QFILE}/answers", json={"answers": {"1": "A"}})
    assert r.status_code == 200, r.text


def _baselines(pid, path):
    r = client.get(f"/projects/{pid}/revisions", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()["baselines"]


def _baseline_text(pid, round_id, path):
    r = client.get(f"/projects/{pid}/revisions/{round_id}", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()["content"]


def test_answers_keep_the_text_the_user_read_before_the_revision(monkeypatch):
    runner = _seed(monkeypatch, "rv1")
    _write(runner, _DOC, "# 페인 포인트\n\n- 재고 확인이 느리다\n")
    _submit("rv1")
    # The agent's turn revises the document.
    _write(runner, _DOC, "# 페인 포인트\n\n- 재고 확인이 느리다\n- 반품 처리가 수작업이다\n")

    [baseline] = _baselines("rv1", _DOC)
    assert baseline["kind"] == "answers"
    assert baseline["source"] == _QFILE
    assert baseline["existed"] is True
    assert _baseline_text("rv1", baseline["id"], _DOC) == "# 페인 포인트\n\n- 재고 확인이 느리다\n"


def test_the_copy_is_taken_before_the_turn_starts(monkeypatch):
    """If the copy were taken after the turn started, what the agent writes could get into it and the
    comparison would show nothing."""
    runner = _seed(monkeypatch, "rv2")
    _write(runner, _DOC, "before\n")
    seen = []
    original = runner.send_message

    def send_message(text, record=None):
        seen.append(any(k.startswith(doc_revisions.ROUNDS_PREFIX) for k in runner._s3.blobs))
        return original(text, record)

    monkeypatch.setattr(runner, "send_message", send_message)
    _submit("rv2")
    assert seen == [True]


def test_an_unchanged_document_offers_no_baseline(monkeypatch):
    runner = _seed(monkeypatch, "rv3")
    _write(runner, _DOC, "same\n")
    _submit("rv3")
    assert _baselines("rv3", _DOC) == []


def test_a_document_written_after_the_answers_is_marked_as_new(monkeypatch):
    runner = _seed(monkeypatch, "rv4")
    _write(runner, _DOC, "exists\n")
    _submit("rv4")
    _write(runner, _SPEC, "# 프로토타입 스펙\n")

    [baseline] = _baselines("rv4", _SPEC)
    assert baseline["existed"] is False
    assert _baseline_text("rv4", baseline["id"], _SPEC) is None


def test_prototype_spec_is_kept_with_the_documents(monkeypatch):
    runner = _seed(monkeypatch, "rv5")
    _write(runner, _SPEC, "- **제품**: 여행 친구\n")
    _submit("rv5")
    _write(runner, _SPEC, "- **제품**: 여행 친구 — 상품을 권하지 않는다\n")
    [baseline] = _baselines("rv5", _SPEC)
    assert _baseline_text("rv5", baseline["id"], _SPEC) == "- **제품**: 여행 친구\n"


def test_records_and_question_files_are_not_documents(monkeypatch):
    runner = _seed(monkeypatch, "rv6")
    _write(runner, "aiplc-docs/audit.md", "# audit\n")
    _write(runner, _DOC, "doc\n")
    _submit("rv6")
    [round_] = asyncio.run(doc_revisions.load_rounds(runner, "rv6"))
    assert set(round_.documents) == {_DOC}
    for path in ("aiplc-docs/audit.md", _QFILE, "uploads/a.md"):
        assert client.get("/projects/rv6/revisions", params={"path": path}).status_code == 400


def test_bodies_are_stored_once(monkeypatch):
    """Retention is unlimited, which is affordable only because an unchanged document is not stored again."""
    runner = _seed(monkeypatch, "rv7")
    _write(runner, _DOC, "stable\n")
    _submit("rv7")
    _submit("rv7")
    blobs = [k for k in runner._s3.blobs if k.startswith(doc_revisions.BLOBS_PREFIX)]
    rounds = [k for k in runner._s3.blobs if k.startswith(doc_revisions.ROUNDS_PREFIX)]
    assert len(blobs) == 1
    assert len(rounds) == 2


def test_changing_a_settled_answer_is_a_boundary_too(monkeypatch):
    runner = _seed(monkeypatch, "rv8")
    qfile = "aiplc-docs/discovery/product-strategy/strategy-questions.md"
    _write(runner, qfile, (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    asyncio.run(registry.get("rv8").put_answers(qfile, {1: "A"}))
    _write(runner, _DOC, "before\n")
    r = client.post("/projects/rv8/handoff/revise", json={"file": qfile, "number": 1, "answer": "B"})
    assert r.status_code == 200, r.text
    _write(runner, _DOC, "after\n")
    [baseline] = _baselines("rv8", _DOC)
    assert baseline["kind"] == "revision"
    assert baseline["source"] == qfile


def test_a_capture_failure_does_not_block_the_answers(monkeypatch):
    runner = _seed(monkeypatch, "rv9")

    async def broken(*args, **kwargs):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(doc_revisions, "capture_round", broken)
    _submit("rv9")
    assert runner.sent


def test_a_bad_round_id_is_refused():
    assert client.get("/projects/x/revisions/..%2Fblobs", params={"path": _DOC}).status_code in (400, 404)
    assert not doc_revisions.is_round_id("../blobs/abc")
    assert doc_revisions.is_round_id("2026-10-27T05-20-11Z-0123abcd")


def _round(rid, docs):
    return doc_revisions.Round(id=rid, at="", kind="answers", source="", documents=docs)


def test_baselines_skip_equal_copies_and_keep_the_newest_of_a_run():
    rounds = [_round("r4", {"d": "C"}),   # equals the current text — nothing to show
              _round("r3", {"d": "B"}),
              _round("r2", {"d": "A"}),
              _round("r1", {"d": "A"}),   # same copy as r2: r2 already shows this comparison
              _round("r0", {})]           # the document did not exist yet
    picked = doc_revisions.baselines_for(rounds, "d", "C")
    assert [r.id for r in picked] == ["r3", "r2", "r0"]


def test_round_ids_sort_by_time():
    a = doc_revisions._new_round_id("2026-10-27T05:20:11.123456+00:00")
    b = doc_revisions._new_round_id("2026-10-27T05:20:12.000001+00:00")
    assert a.startswith("2026-10-27T05-20-11Z-")
    assert doc_revisions.is_round_id(a)
    assert a < b
