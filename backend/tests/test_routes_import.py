# backend/tests/test_routes_import.py — restores a bundle as a project.
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.agent.session_store import project_transcript_prefix
from aipds.import_staging import MAX_IMPORT_BYTES
from aipds.project_bundle import MANIFEST_NAME, build_manifest
from aipds.project_import import (
    WARN_MODEL_UNAVAILABLE,
    WARN_SURVEY_TOKENS_REISSUED,
    WARN_UNKNOWN_ENTRIES,
)
from aipds.survey.store import TOKEN_INDEX_PREFIX
from aipds.workspace import Workspace
from fakes.fake_import_staging import FakeImportStaging
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

SOURCE_PID = "origin-proj"
SLUG = "demo"
SEED_MODEL = "global.anthropic.claude-sonnet-5"


def _bundle(entries: dict[str, bytes] | None = None, **manifest_over) -> bytes:
    """A single bundle zip. `manifest_over` overrides manifest fields."""
    manifest = json.loads(build_manifest(
        exported_at="2026-09-12T00:00:00+00:00",
        source_project_id=SOURCE_PID,
        project={"name": "원본 프로젝트", "created_at": "2026-08-01T00:00:00+00:00",
                 "model_id": SEED_MODEL, "language": "en"},
        counts={}, prototypes=[]))
    manifest.update(manifest_over)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
        for name, body in (entries or {}).items():
            zf.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    stores: dict[str, FakeS3Store] = {}
    root = FakeS3Store()          # projects/ manifests
    surveys = FakeS3Store()       # bucket root (token index)
    staging = FakeImportStaging()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory",
                        lambda pid: stores.setdefault(pid, FakeS3Store()))
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: surveys)
    monkeypatch.setattr(app_module, "import_staging", lambda: staging)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    created: list[str] = []

    def _track(pid: str) -> str:
        created.append(pid)
        return pid

    yield {"stores": stores, "root": root, "surveys": surveys,
           "staging": staging, "proto_root": tmp_path, "track": _track}
    for pid in {SOURCE_PID, *created}:
        app_module.registry.remove(pid)


def _upload(env, body: bytes, upload_id: str = "a" * 32) -> str:
    env["staging"].objects[upload_id] = body
    return upload_id


def _import(env, body: bytes, project_id: str | None = None,
            upload_id: str = "a" * 32):
    uid = _upload(env, body, upload_id)
    payload: dict = {"upload_id": uid}
    if project_id is not None:
        payload["project_id"] = project_id
    return client.post("/project-imports", json=payload)


# ---- presign ----


def test_upload_request_returns_a_presigned_url(env):
    resp = client.post("/project-imports/uploads", json={"size_bytes": 1024})

    assert resp.status_code == 201
    body = resp.json()
    assert body["url"].startswith("https://")
    assert body["content_type"] == "application/zip"
    assert body["max_bytes"] == MAX_IMPORT_BYTES
    assert env["staging"].presigned == [body["upload_id"]]


def test_upload_request_refuses_an_oversized_bundle_before_it_starts(env):
    resp = client.post("/project-imports/uploads",
                       json={"size_bytes": MAX_IMPORT_BYTES + 1})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_too_large"
    assert env["staging"].presigned == []


def test_upload_request_needs_a_positive_size(env):
    assert client.post("/project-imports/uploads",
                       json={"size_bytes": 0}).status_code == 400


def test_import_is_unavailable_without_a_bucket(env, monkeypatch):
    """If this degraded silently, the user would find out only after uploading hundreds
    of MB."""
    def _no_bucket():
        raise RuntimeError("project import requires AIPDS_S3_BUCKET")

    monkeypatch.setattr(app_module, "import_staging", _no_bucket)

    resp = client.post("/project-imports/uploads", json={"size_bytes": 10})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "import_unavailable"


# ---- Basic path ----


def test_import_registers_the_project_under_the_bundles_own_id(env):
    resp = _import(env, _bundle({"project/aiplc-docs/audit.md": b"# audit"}))

    assert resp.status_code == 201
    body = resp.json()
    assert body["project_id"] == SOURCE_PID
    assert body["source_project_id"] == SOURCE_PID
    assert app_module.registry.is_registered(SOURCE_PID)
    assert env["stores"][SOURCE_PID].blobs["aiplc-docs/audit.md"] == "# audit"


def test_import_preserves_name_created_at_and_language(env):
    """Keep the creation date as in the original: 'this project started in August' is a fact
    about that project, and list ordering follows that fact."""
    resp = _import(env, _bundle())

    assert resp.json()["name"] == "원본 프로젝트"
    assert resp.json()["language"] == "en"
    assert app_module.registry.get_created_at(SOURCE_PID) == \
        "2026-08-01T00:00:00+00:00"
    manifest = json.loads(env["root"].blobs[f"{SOURCE_PID}/project.json"])
    assert manifest["created_at"] == "2026-08-01T00:00:00+00:00"
    assert manifest["language"] == "en"


def test_the_importer_owns_the_imported_project(env):
    """A bundle does not carry the owner; a username from another instance may be a
    different person here. The person who imported it (in local dev, the requester
    local-dev) becomes the owner."""
    _import(env, _bundle())

    assert app_module.registry.get_owner(SOURCE_PID) == "local-dev"
    manifest = json.loads(env["root"].blobs[f"{SOURCE_PID}/project.json"])
    assert (manifest["owner"], manifest["members"]) == ("local-dev", [])


def test_manifest_is_written_and_the_project_survives_a_restart(env):
    """project.json is the source of truth for 'this project exists' (restore_projects)."""
    _import(env, _bundle())

    assert f"{SOURCE_PID}/project.json" in env["root"].blobs


def test_import_under_a_new_id_moves_the_transcript_with_it(env):
    """The uuid5 in transcript keys is derived from the project id; without rewriting
    it, the reader of the history sees an empty prefix, and that failure is silent, with
    no error."""
    target = env["track"]("copied-proj")
    body = _bundle({
        "project/discovery/transcript/main/00000001.jsonl": b'{"type":"user"}',
        "project/discovery/transcript/sub/agent-a/00000001.jsonl": b'{"type":"x"}',
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    keys = set(env["stores"][target].blobs.keys())
    prefix = project_transcript_prefix(target)
    assert f"{prefix}main/00000001.jsonl" in keys
    assert f"{prefix}sub/agent-a/00000001.jsonl" in keys
    # Nothing was written under the original project's session prefix.
    assert not any(k.startswith(project_transcript_prefix(SOURCE_PID))
                   for k in keys)


def test_import_lands_prototype_source_on_local_disk(env):
    """For the card to show as built, {proto_root}/{pid}/{slug}/prototype/ must not be
    empty (proto/session.has_build_output)."""
    target = env["track"]("with-proto")
    body = _bundle({
        f"prototypes/{SLUG}/source/prototype/app.tsx": b"export default null",
        f"prototypes/{SLUG}/source/prototype/package.json": b'{"name":"p"}',
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    built = env["proto_root"] / target / SLUG / "prototype"
    assert (built / "app.tsx").read_bytes() == b"export default null"
    assert (built / "package.json").exists()
    assert resp.json()["counts"]["prototypes"] == 1


def test_import_deletes_the_staged_bundle_on_success(env):
    uid = _upload(env, _bundle())

    client.post("/project-imports", json={"upload_id": uid})

    assert env["staging"].objects == {}


# ---- Survey tokens ----


def _survey_bundle(token: str = "original-token") -> bytes:
    definition = json.dumps({"token": token, "status": "open", "slug": SLUG,
                             "project_id": SOURCE_PID, "created_at": "c",
                             "title": "t", "hypothesis": "h",
                             "questions": [{"id": "q1"}]}).encode("utf-8")
    return _bundle({
        f"project/prototypes/{SLUG}/survey/questionnaire.json": definition,
        f"project/prototypes/{SLUG}/survey/responses/r1.json": b'{"answers":{}}',
    })


def test_survey_token_is_reissued_and_indexed_to_the_new_project(env):
    """If the original token were reused as is, the original project's live survey link
    in the same bucket would pass to the new project; responses pile up in the wrong
    place with no error."""
    target = env["track"]("survey-copy")

    resp = _import(env, _survey_bundle(), project_id=target)

    assert resp.status_code == 201
    key = f"prototypes/{SLUG}/survey/questionnaire.json"
    definition = json.loads(env["stores"][target].blobs[key])
    assert definition["token"] != "original-token"
    assert definition["project_id"] == target
    index = json.loads(
        env["surveys"].blobs[f"{TOKEN_INDEX_PREFIX}{definition['token']}.json"])
    assert index == {"project_id": target, "slug": SLUG}
    # No index is created for the original token.
    assert f"{TOKEN_INDEX_PREFIX}original-token.json" not in env["surveys"].blobs
    assert {"code": WARN_SURVEY_TOKENS_REISSUED, "count": 1} in \
        resp.json()["warnings"]


def test_survey_responses_come_along(env):
    target = env["track"]("survey-answers")

    _import(env, _survey_bundle(), project_id=target)

    assert env["stores"][target].blobs[
        f"prototypes/{SLUG}/survey/responses/r1.json"] == '{"answers":{}}'


def test_archived_survey_token_changes_but_gets_no_live_index(env):
    """There is no reason for a past round's link to stay live, but if the original token were
    kept, it would have the same value as the original instance's link."""
    target = env["track"]("survey-archive")
    archived = json.dumps({"token": "old-archived", "status": "closed",
                           "slug": SLUG, "project_id": SOURCE_PID,
                           "created_at": "c", "title": "t", "hypothesis": "h",
                           "questions": [{"id": "q1"}]}).encode("utf-8")
    body = _bundle({
        f"project/prototypes/{SLUG}/survey/archive/2026-01-01/questionnaire.json":
            archived,
    })

    _import(env, body, project_id=target)

    key = f"prototypes/{SLUG}/survey/archive/2026-01-01/questionnaire.json"
    definition = json.loads(env["stores"][target].blobs[key])
    assert definition["token"] != "old-archived"
    assert definition["project_id"] == target
    assert env["surveys"].blobs.keys() == set()


def test_unreadable_questionnaire_fails_the_whole_import(env):
    """If the definition cannot be read, the token cannot be changed either; leaving it as is
    keeps the original's token alive."""
    target = env["track"]("bad-survey")
    body = _bundle({
        f"project/prototypes/{SLUG}/survey/questionnaire.json": b"not json",
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 400
    assert not app_module.registry.is_registered(target)
    assert env["stores"][target].blobs.keys() == set()


# ---- Model fallback ----


def test_a_model_this_instance_cannot_select_is_dropped_with_a_warning(env):
    """If this degraded silently, the project would run on 'a model nobody chose', the quietest
    failure in this feature."""
    target = env["track"]("foreign-model")
    body = _bundle(project={"name": "n", "created_at": "c",
                            "model_id": "some.other.model", "language": "ko"})

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    assert resp.json()["model_id"] is None
    assert {"code": WARN_MODEL_UNAVAILABLE, "count": 1} in resp.json()["warnings"]
    assert app_module.registry.get_model_id(target) is None


def test_the_source_projects_effort_comes_with_its_model(env):
    """effort is a value copied from the original as a pair with the model; the import
    brings it over unchanged."""
    target = env["track"]("with-effort")
    body = _bundle(project={"name": "n", "created_at": "c", "model_id": SEED_MODEL,
                            "language": "ko", "effort": "high"})
    resp = _import(env, body, project_id=target)
    assert resp.status_code == 201
    assert resp.json()["effort"] == "high"
    assert app_module.registry.get_effort(target) == "high"


def test_a_bundle_from_before_effort_keeps_the_cli_default(env):
    """Bundles from before this field have no key; it stays exactly how the original
    actually ran (the CLI default)."""
    target = env["track"]("no-effort")
    resp = _import(env, _bundle(), project_id=target)
    assert resp.json()["effort"] is None


def test_effort_is_dropped_with_an_unavailable_model(env):
    """Clearing the model but keeping effort would attach it to another model, a
    combination nobody chose."""
    target = env["track"]("foreign-effort")
    body = _bundle(project={"name": "n", "created_at": "c", "model_id": "some.other.model",
                            "language": "ko", "effort": "max"})
    resp = _import(env, body, project_id=target)
    assert resp.json()["model_id"] is None and resp.json()["effort"] is None


def test_a_selectable_model_is_kept_without_a_warning(env):
    resp = _import(env, _bundle())

    assert resp.json()["model_id"] == SEED_MODEL
    assert resp.json()["warnings"] == []


# ---- Conflicts ----


def test_an_existing_id_is_409_and_keeps_the_upload(env):
    """An id conflict can only be detected by reading the manifest; deleting the staging here
    would force the user to re-upload the whole bundle just to change one id."""
    client.post("/projects", json={"project_id": SOURCE_PID})
    uid = _upload(env, _bundle())

    resp = client.post("/project-imports", json={"upload_id": uid})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "project_exists"
    assert uid in env["staging"].objects


def test_retrying_a_409_with_a_new_id_needs_no_re_upload(env):
    client.post("/projects", json={"project_id": SOURCE_PID})
    target = env["track"]("second-copy")
    uid = _upload(env, _bundle({"project/aiplc-docs/audit.md": b"# audit"}))
    assert client.post("/project-imports",
                       json={"upload_id": uid}).status_code == 409

    resp = client.post("/project-imports",
                       json={"upload_id": uid, "project_id": target})

    assert resp.status_code == 201
    assert env["stores"][target].blobs["aiplc-docs/audit.md"] == "# audit"


# ---- Refusals ----


def test_a_missing_upload_is_reported_as_such(env):
    """What the user must do is not 'pick a different file' but 'upload again'."""
    resp = client.post("/project-imports", json={"upload_id": "b" * 32})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "upload_missing"


@pytest.mark.parametrize("upload_id", [
    "../projects/victim/project", "a" * 31, "A" * 32, "", "not-hex-" + "0" * 24,
])
def test_a_forged_upload_id_is_refused(env, upload_id):
    """This value becomes an S3 key; enforcing its shape leaves the client no room to choose
    the key."""
    resp = client.post("/project-imports", json={"upload_id": upload_id})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"


def test_a_non_zip_upload_is_refused_and_discarded(env):
    uid = _upload(env, b"this is not a zip")

    resp = client.post("/project-imports", json={"upload_id": uid})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"
    assert env["staging"].objects == {}


def test_a_zip_without_a_manifest_is_refused(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("project/aiplc-docs/audit.md", "# audit")

    resp = _import(env, buf.getvalue())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"


def test_another_kind_of_aipds_zip_is_refused(env):
    """Dropping an artifact archive or a prototype handoff zip into the import slot is a
    common mistake."""
    resp = _import(env, _bundle(kind="aipds-artifacts"))

    assert resp.status_code == 400


def test_a_newer_schema_version_is_refused(env):
    """Interpreting an unknown version on a best-effort basis yields a half-imported project,
    and that state is indistinguishable from a normal one on screen."""
    resp = _import(env, _bundle(schema_version=99))

    assert resp.status_code == 400


def test_a_traversal_project_id_is_refused(env):
    resp = _import(env, _bundle(), project_id="../evil")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"


def test_a_traversal_entry_is_refused_and_nothing_is_written(env):
    target = env["track"]("zip-slip")
    body = _bundle({"project/aiplc-docs/ok.md": b"# ok"})
    # zipfile stores the names we put in as is; this imitates an attacker's zip.
    buf = io.BytesIO(body)
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("project/../../../../etc/passwd", "root:x:0:0")

    resp = _import(env, buf.getvalue(), project_id=target)

    assert resp.status_code == 400
    assert not app_module.registry.is_registered(target)
    assert env["stores"][target].blobs.keys() == set()


def test_an_oversized_upload_is_refused_before_download(env, monkeypatch):
    monkeypatch.setattr("aipds.routes.transfer.MAX_IMPORT_BYTES", 10)
    uid = _upload(env, _bundle())

    resp = client.post("/project-imports", json={"upload_id": uid})

    assert resp.status_code == 413
    assert resp.json()["detail"] == "export_too_large"
    assert env["staging"].objects == {}


def test_unrecognised_entries_are_skipped_but_reported(env):
    """Things like `__MACOSX/` added when a user re-compresses a zip are common. Skip them,
    but not silently."""
    target = env["track"]("extra-entries")
    body = _bundle({
        "project/aiplc-docs/audit.md": b"# audit",
        "__MACOSX/._audit.md": b"junk",
        "readme.txt": b"hello",
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    assert {"code": WARN_UNKNOWN_ENTRIES, "count": 2} in resp.json()["warnings"]
    assert env["stores"][target].blobs.keys() == {"aiplc-docs/audit.md"}


def test_a_manifest_write_failure_rolls_the_import_back(env, monkeypatch):
    """Without a manifest, it is a project that vanishes after a restart; do not create one
    silently (the same judgment as the creation route)."""
    target = env["track"]("rollback-proj")

    class _Boom(FakeS3Store):
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: _Boom())
    body = _bundle({
        "project/aiplc-docs/audit.md": b"# audit",
        f"prototypes/{SLUG}/source/prototype/app.tsx": b"x",
        f"project/prototypes/{SLUG}/survey/questionnaire.json": json.dumps(
            {"token": "t", "slug": SLUG, "project_id": SOURCE_PID}).encode(),
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 500
    assert not app_module.registry.is_registered(target)
    # Everything written was rolled back: S3 objects, the token index, the local source tree.
    assert env["stores"][target].blobs.keys() == set()
    assert env["surveys"].blobs.keys() == set()
    assert not (env["proto_root"] / target).exists()
