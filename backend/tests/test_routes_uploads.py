import asyncio
import io
import re
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner
from aipds.parsers.uploads import MAX_UPLOAD_BYTES

client = TestClient(app_module.app)

_KEY_RE = re.compile(r"^uploads/[0-9a-f]{8}/(.+)$")

def _local_project(monkeypatch, pid):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})

def test_upload_md_saved_under_a_uuid_directory(monkeypatch):
    _local_project(monkeypatch, "u1")
    r = client.post("/projects/u1/uploads",
                    files={"file": ("의견.md", io.BytesIO("# 의견".encode()), "text/markdown")})
    assert r.status_code == 200
    body = r.json()
    m = _KEY_RE.match(body["path"])
    assert m and m.group(1) == "의견.md.md"
    assert body["truncated"] is False
    # Verify storage: via the same runner's read_file (there is no files API, so go directly through the workspace registry).
    # The _ensure_event_loop autouse fixture in conftest guarantees a loop.
    ws = app_module.registry.get("u1")
    assert asyncio.get_event_loop().run_until_complete(
        ws.runner.read_file(body["path"])) == "# 의견"

def test_same_name_uploads_do_not_overwrite(monkeypatch):
    """The regression: the old list-then-write path let two uploads of one
    name land on the same key, and the later write silently deleted the
    earlier file."""
    _local_project(monkeypatch, "u2")
    paths = []
    for _ in range(2):
        r = client.post("/projects/u2/uploads",
                        files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")})
        paths.append(r.json()["path"])
    assert paths[0] != paths[1]

    ws = app_module.registry.get("u2")
    loop = asyncio.get_event_loop()
    for p in paths:
        assert loop.run_until_complete(ws.runner.read_file(p)) == "x"

def test_upload_rejects_big_and_unsupported(monkeypatch):
    _local_project(monkeypatch, "u3")
    big = io.BytesIO(b"0" * (MAX_UPLOAD_BYTES + 1))
    assert client.post("/projects/u3/uploads",
                       files={"file": ("big.txt", big, "text/plain")}).status_code == 413
    assert client.post("/projects/u3/uploads",
                       files={"file": ("run.exe", io.BytesIO(b"MZ"), "application/x-msdownload")}
                       ).status_code == 415

def test_upload_unknown_project_404():
    assert client.post("/projects/ghost/uploads",
                       files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")}).status_code == 404

def test_upload_corrupt_xlsx_415_not_500(monkeypatch):
    # Reviewer-flagged gap: a valid extension with garbage bytes must 415, not
    # 500 (openpyxl's zipfile.BadZipFile must never leak past convert()).
    _local_project(monkeypatch, "u4")
    r = client.post("/projects/u4/uploads",
                    files={"file": ("bad.xlsx", io.BytesIO(b"not a real xlsx"),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 415

def test_upload_spoofed_content_length_rejected_before_body_read(monkeypatch):
    # Reviewer-flagged gap: a Content-Length far beyond the upload cap should be
    # rejected from the header alone, before the body is spooled/read. This
    # exercises the pre-check branch directly (TestClient/httpx honors an
    # explicit Content-Length override even though the body is small) --
    # the post-read len(data) check (test_upload_rejects_big_and_unsupported)
    # remains the authoritative fallback for a truthful Content-Length.
    _local_project(monkeypatch, "u5")
    r = client.post("/projects/u5/uploads",
                    files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
                    headers={"Content-Length": str(MAX_UPLOAD_BYTES + 1024 * 1024)})
    assert r.status_code == 413


def _stored(pid, path):
    ws = app_module.registry.get(pid)
    return asyncio.get_event_loop().run_until_complete(ws.runner._s3.get_bytes(path))


def test_an_image_is_stored_as_its_original_bytes(monkeypatch):
    _local_project(monkeypatch, "u6")
    png = b"\x89PNG\r\n\x1a\n\xff\xfe\x00binary"
    r = client.post("/projects/u6/uploads",
                    files={"file": ("화면.png", io.BytesIO(png), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert _KEY_RE.match(body["path"]).group(1) == "화면.png"
    assert body["original"] is None and body["chars"] == 0
    assert _stored("u6", body["path"]) == png


def test_a_pdf_answers_with_its_text_and_points_at_its_original(monkeypatch):
    from test_uploads_parser import _pdf_bytes
    _local_project(monkeypatch, "u7")
    pdf = _pdf_bytes("Codeword PELICAN")
    r = client.post("/projects/u7/uploads",
                    files={"file": ("요구사항.pdf", io.BytesIO(pdf), "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert _KEY_RE.match(body["path"]).group(1) == "요구사항.pdf.md"
    assert body["original"] == body["path"][:-len(".md")]
    assert b"PELICAN" in _stored("u7", body["path"])
    assert _stored("u7", body["original"]) == pdf


def test_an_image_extension_on_other_bytes_is_415(monkeypatch):
    _local_project(monkeypatch, "u8")
    r = client.post("/projects/u8/uploads",
                    files={"file": ("x.png", io.BytesIO(b"<svg/>"), "image/png")})
    assert r.status_code == 415


def test_a_file_at_the_limit_is_accepted(monkeypatch):
    _local_project(monkeypatch, "u9")
    r = client.post("/projects/u9/uploads",
                    files={"file": ("big.txt", io.BytesIO(b"0" * MAX_UPLOAD_BYTES), "text/plain")})
    assert r.status_code == 200
    assert r.json()["truncated"] is True


def test_the_limit_is_10mb_and_the_message_says_so(monkeypatch):
    assert MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    _local_project(monkeypatch, "u10")
    r = client.post("/projects/u10/uploads",
                    files={"file": ("big.txt", io.BytesIO(b"0" * (MAX_UPLOAD_BYTES + 1)), "text/plain")})
    assert r.status_code == 413 and r.json()["detail"] == "file exceeds 10MB limit"
