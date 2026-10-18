# backend/tests/test_routes_projects_delete.py
import json

import pytest
from fastapi.testclient import TestClient
from aipds import app as app_module
from aipds.workspace import Workspace
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


class _FakeRunner:
    def __init__(self):
        self.stopped = 0

    async def stop(self):
        self.stopped += 1


class _FakeHost:
    """Stand-in for the prototype's physical state (local tree, hosting, tokens). `purge`
    really deletes the tree; with only an in-memory counter, a test asserting "the
    build tree is left behind" would verify nothing."""

    def __init__(self, root=None):
        self.root = root
        self.purged: list[tuple[str, str]] = []
        self.purge_exc: Exception | None = None
        self.purged_projects: list[str] = []
        self.purge_project_exc: Exception | None = None

    def slugs(self, pid):
        found: set[str] = set()
        if self.root is not None:
            base = self.root / pid
            if base.is_dir():
                found |= {c.name for c in base.iterdir() if c.is_dir()}
        return sorted(found)

    async def purge(self, pid, slug):
        self.purged.append((pid, slug))
        if self.purge_exc is not None:
            raise self.purge_exc
        if self.root is not None:
            import shutil
            shutil.rmtree(self.root / pid / slug, ignore_errors=True)

    async def purge_project(self, pid):
        """Clean up the parent directory. It **really** deletes, like the real thing; with
        only a counter, a test asserting "no empty shell is left behind" would be
        vacuous (same reason as `purge`)."""
        self.purged_projects.append(pid)
        if self.purge_project_exc is not None:
            raise self.purge_project_exc
        if self.root is not None:
            import shutil
            shutil.rmtree(self.root / pid, ignore_errors=True)


class _FakeSession:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


@pytest.fixture(autouse=True)
def _proto_wiring(monkeypatch, tmp_path):
    """The delete path runs prototype cleanup, so all of its dependencies are wired to fakes too.

    Keeping s3_store_factory (project prefix) and surveys_root_s3_factory (bucket root)
    **separate** is load-bearing: the survey token index lives at the root, and merging
    the two into one fake would make the assertion "the index was reclaimed" vacuous
    (same reason as proto_env in test_routes_prototypes.py).
    """
    project_s3, surveys_root = FakeS3Store(), FakeS3Store()
    host = _FakeHost(root=tmp_path)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: project_s3)
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: surveys_root)
    monkeypatch.setattr(app_module, "proto_host", lambda: host)
    sessions_backup = dict(app_module.proto_sessions)
    app_module.proto_sessions.clear()
    yield {"project_s3": project_s3, "surveys_root": surveys_root,
           "host": host, "proto_root": tmp_path}
    app_module.proto_sessions.clear()
    app_module.proto_sessions.update(sessions_backup)


def _seed_project(pid: str, sessions: FakeS3Store, root: FakeS3Store) -> _FakeRunner:
    runner = _FakeRunner()
    app_module.registry.register(pid)
    app_module.registry.attach(pid, Workspace(runner))
    sessions.blobs[f"session_{pid}/agents/agent_default/messages/message_0.json"] = "{}"
    root.blobs[f"{pid}/project.json"] = "{}"
    root.blobs[f"{pid}/aiplc-docs/audit.md"] = "x"
    return runner


def _seed_prototype(env, pid: str, slug: str, token: str) -> _FakeSession:
    """Create a prototype that **has physical state**, not one with only a spec:
    an S3 survey (questions + root token index), a local build tree + token file, and an active session."""
    env["project_s3"].blobs[f"prototypes/{slug}/survey/questionnaire.json"] = json.dumps(
        {"token": token, "slug": slug, "project_id": pid})
    env["surveys_root"].blobs[f"surveys/by-token/{token}.json"] = json.dumps(
        {"project_id": pid, "slug": slug})
    tree = env["proto_root"] / pid / slug / "prototype"
    tree.mkdir(parents=True)
    (tree / "package.json").write_text("{}", encoding="utf-8")
    (env["proto_root"] / pid / slug / ".proto-token").write_text(token, encoding="utf-8")
    session = _FakeSession()
    app_module.proto_sessions[(pid, slug)] = session
    return session


def test_delete_removes_registry_vm_and_s3(monkeypatch):
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    runner = _seed_project("del-1", sessions, root)

    r = client.delete("/projects/del-1")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert runner.stopped == 1
    assert not any(k.startswith("session_del-1/") for k in sessions.blobs)
    assert not any(k.startswith("del-1/") for k in root.blobs)
    assert not app_module.registry.is_registered("del-1")


def test_delete_purges_prototype_runtime_state_and_root_token_index(monkeypatch, _proto_wiring):
    """Deleting a project also cleans up the prototype's **physical state**.

    These are things that deleting S3 projects/{pid}/ does not reach: the local build tree,
    access tokens, active build sessions, and the survey token index at the bucket **root**.
    If they remain, trees of hundreds of MB pile up on EBS, and preview and survey links
    already shared keep opening even for a deleted project.
    """
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    pid = "del-proto"
    _seed_project(pid, sessions, root)
    session = _seed_prototype(env, pid, "checkout", "tok-checkout")
    # Root index of preview tokens (proto/store.py); lives at the same root as the survey token index.
    import asyncio
    from aipds.proto.store import PrototypeStore, resolve_token
    asyncio.run(PrototypeStore(app_module.s3_store_factory(pid),
                               root=env["surveys_root"], project_id=pid)
                .save_token("checkout", "preview-x"))

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 200
    assert asyncio.run(resolve_token(env["surveys_root"], "preview-x")) is None
    assert session.closed == 1
    assert (pid, "checkout") not in app_module.proto_sessions
    assert env["host"].purged == [(pid, "checkout")]
    assert not (env["proto_root"] / pid / "checkout").exists()
    # Root-scoped index: there is no reverse lookup, so it can only be reclaimed by reading the question file first.
    assert "surveys/by-token/tok-checkout.json" not in env["surveys_root"].blobs
    assert not app_module.registry.is_registered(pid)


def test_delete_cleans_every_slug_of_the_project(monkeypatch, _proto_wiring):
    """With several slugs, every one is processed. Processing only one silently leaves the rest behind."""
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    pid = "del-multi"
    _seed_project(pid, sessions, root)
    _seed_prototype(env, pid, "alpha", "tok-a")
    _seed_prototype(env, pid, "beta", "tok-b")
    # A slug with only a local tree and no record (e.g., rebuilt after a reset); missed without local enumeration.
    (env["proto_root"] / pid / "gamma").mkdir(parents=True)

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 200
    assert env["host"].purged == [(pid, "alpha"), (pid, "beta"), (pid, "gamma")]
    assert list(env["surveys_root"].blobs) == []          # both token indexes reclaimed
    assert not (env["proto_root"] / pid / "gamma").exists()


def test_delete_500_keeps_s3_and_registry_when_build_tree_purge_fails(monkeypatch, _proto_wiring):
    """A cleanup failure is not silently ignored: 500, and the state stays retryable.

    If S3 were deleted, the question file needed to reclaim the remaining token index
    would be gone, and that index would become permanently unreachable. So on failure,
    S3 and the registry are left as they are.
    """
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    pid = "del-stuck"
    _seed_project(pid, sessions, root)
    _seed_prototype(env, pid, "checkout", "tok-stuck")
    env["host"].purge_exc = RuntimeError("purge left residue")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 500
    assert app_module.registry.is_registered(pid)
    assert f"{pid}/project.json" in root.blobs
    assert any(k.startswith(f"session_{pid}/") for k in sessions.blobs)


def test_delete_keeps_build_tree_when_survey_purge_fails(monkeypatch, _proto_wiring):
    """A slug whose survey purge failed does not have its build tree touched either.

    Same judgment as the gate on the reset path: if only the physical state is deleted
    while the survey (and its live public link) remains, the questions a retry would
    reclaim are still there, but there is nothing left to host.
    """
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)

    class _ExplodingSurveyStore:
        def __init__(self, pid, slug):
            pass

        async def purge(self):
            raise RuntimeError("survey s3 down")

    monkeypatch.setattr(app_module, "survey_store_factory",
                        lambda pid, slug: _ExplodingSurveyStore(pid, slug))
    pid = "del-survey-fail"
    _seed_project(pid, sessions, root)
    _seed_prototype(env, pid, "checkout", "tok-keep")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 500
    assert env["host"].purged == []                      # this slug goes no further
    assert (env["proto_root"] / pid / "checkout").is_dir()
    assert "surveys/by-token/tok-keep.json" in env["surveys_root"].blobs
    assert app_module.registry.is_registered(pid)


def test_delete_cleans_local_prototypes_without_durable_storage(monkeypatch, _proto_wiring):
    """Even with no bucket configured (local/test), local physical cleanup runs: the build
    tree and sessions exist independently of S3. Only the survey step is skipped.

    setenv("") is required: aipds.app loads backend/.env at startup, so on a dev box
    AIPDS_S3_BUCKET is already set, and then this test takes the durable path and skips
    the branch it meant to verify (observed: without this line, the survey index was
    deleted). proto_env in test_routes_prototypes.py has the same line for the same
    reason.
    """
    env = _proto_wiring
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    pid = "del-nodurable"
    app_module.registry.register(pid)
    session = _seed_prototype(env, pid, "checkout", "tok-nd")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 200
    assert session.closed == 1
    assert env["host"].purged == [(pid, "checkout")]
    assert not (env["proto_root"] / pid / "checkout").exists()
    # Not durable, so the survey index is not touched (that project is not in S3).
    assert "surveys/by-token/tok-nd.json" in env["surveys_root"].blobs
    assert not app_module.registry.is_registered(pid)


def test_delete_unknown_project_404():
    assert client.delete("/projects/no-such").status_code == 404


def test_delete_continues_when_stop_fails(monkeypatch):
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    runner = _seed_project("del-2", sessions, root)

    async def _boom():
        raise RuntimeError("runner stuck")

    runner.stop = _boom  # type: ignore[assignment]
    r = client.delete("/projects/del-2")
    assert r.status_code == 200  # a stop failure does not block deletion
    assert not app_module.registry.is_registered("del-2")


def test_delete_returns_500_and_keeps_registry_on_s3_failure(monkeypatch):
    class _ExplodingStore(FakeS3Store):
        async def delete_prefix(self, prefix):
            raise RuntimeError("s3 down")

    sessions, root = _ExplodingStore(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    _seed_project("del-3", sessions, root)

    r = client.delete("/projects/del-3")
    assert r.status_code == 500
    assert app_module.registry.is_registered("del-3")  # kept -> retryable


def test_delete_registered_but_unbooted_project(monkeypatch):
    # Deletion must work even right after restore (no workspace)
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    app_module.registry.register("del-4")
    root.blobs["del-4/project.json"] = "{}"

    r = client.delete("/projects/del-4")
    assert r.status_code == 200
    assert not app_module.registry.is_registered("del-4")


def test_delete_stops_runner_attached_by_concurrent_boot_during_s3_await(monkeypatch):
    """Reverse race: DELETE runs on a restored-but-not-booted (no workspace) project and
    skips the stop block; then, while the S3 delete is awaited, a concurrent
    ensure_workspace finishes initialization and attaches a live workspace via
    registry.attach. If the workspace returned by the final registry.remove(pid) is not
    stopped, the runner just created leaks."""
    pid = "del-race"
    runner2 = _FakeRunner()

    class _RaceSessionsStore(FakeS3Store):
        async def delete_prefix(self, prefix):
            # Simulate the moment a concurrent boot attaches while the project is still registered (is_registered).
            app_module.registry.attach(pid, Workspace(runner2))
            return await super().delete_prefix(prefix)

    sessions, root = _RaceSessionsStore(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    app_module.registry.register(pid)
    root.blobs[f"{pid}/project.json"] = "{}"

    r = client.delete(f"/projects/{pid}")
    assert r.status_code == 200
    assert runner2.stopped == 1
    assert not app_module.registry.is_registered(pid)


# ---- Leftovers of local physical state ----
#
# Observed (2026-08-19, deployed instance): `/opt/aipds/workspaces/` still held the
# directories of 6 deleted projects (2.35MB total), none of which existed in S3 `projects/`.
# `/opt/aipds/protos/` still held 3 empty parent directories.

def test_delete_removes_the_workspace_dir_of_a_project_never_booted_this_process(
        monkeypatch, tmp_path):
    """Deleting **a project never opened since the restart** must still remove its
    workspace.

    This is the common path. At startup `app.py` restores only the list from the S3
    manifest, calling only `registry.register()` and not `attach()` (the workspace is
    lazily initialized on the first request). So right after a restart every project has
    `has_workspace() == False`, and since the old delete path gated local cleanup on that
    flag (the rmtree lived only inside `runner.stop()`), the directory on disk was left
    as is. Meanwhile the user had been promised that "chat history and documents are
    permanently deleted".
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    ws_root = tmp_path / "workspaces"
    monkeypatch.setattr(app_module, "_workspaces_dir", lambda: ws_root)

    # Register only, no attach. This is the state right after a restart.
    app_module.registry.register("del-ws")
    root.blobs["del-ws/project.json"] = "{}"
    left_behind = ws_root / "del-ws" / "aiplc-docs"
    left_behind.mkdir(parents=True)
    (left_behind / "audit.md").write_text("secrets", encoding="utf-8")

    r = client.delete("/projects/del-ws")

    assert r.status_code == 200, r.text
    assert not (ws_root / "del-ws").exists(), "워크스페이스 디렉터리가 남았다"


def test_delete_removes_the_workspace_dir_when_the_runner_is_attached(
        monkeypatch, tmp_path):
    """The normal, attached path must give the same result: a symmetry check.

    Here `runner.stop()` already deletes, but that rmtree is bound up with driver
    shutdown, so if stop fails it is skipped too (that failure is swallowed on purpose).
    Path-based deletion backs it up.
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    ws_root = tmp_path / "workspaces"
    monkeypatch.setattr(app_module, "_workspaces_dir", lambda: ws_root)

    class _BrokenRunner:
        async def stop(self):
            raise RuntimeError("driver disconnect failed")

    app_module.registry.register("del-ws2")
    app_module.registry.attach("del-ws2", Workspace(_BrokenRunner()))
    root.blobs["del-ws2/project.json"] = "{}"
    (ws_root / "del-ws2").mkdir(parents=True)

    r = client.delete("/projects/del-ws2")

    assert r.status_code == 200, r.text
    assert not (ws_root / "del-ws2").exists()


def test_delete_removes_the_projects_prototype_root(monkeypatch, _proto_wiring):
    """After all slugs are deleted, the **parent directory** must be gone too.

    `host.purge` deletes only `{root}/{pid}/{slug}` (host.py), so an empty shell was left
    behind. Deleting the parent **after** the slug loop matters: deleting it first pulls
    the tree out from under a running `npm start`, orphaning the process, which keeps
    holding its port (the same risk that makes `host.purge` call `stop` first).
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    monkeypatch.setattr(app_module, "_workspaces_dir",
                        lambda: _proto_wiring["proto_root"] / "ws")
    _seed_project("del-pr", sessions, root)
    _seed_prototype(_proto_wiring, "del-pr", "demo", "tok-pr")

    r = client.delete("/projects/del-pr")

    assert r.status_code == 200, r.text
    assert not (_proto_wiring["proto_root"] / "del-pr").exists(), (
        "프로토타입 부모 디렉터리가 남았다")


def test_delete_does_not_purge_the_prototype_root_when_a_slug_failed(
        monkeypatch, _proto_wiring):
    """If one slug fails, the parent is not deleted.

    Deleting the parent would also remove the remaining slugs' build trees, leaving
    nothing for a retry to reclaim; same judgment as the per-slug gate (`continue` on
    survey purge failure). And since `purge_project` stops nothing, if the failed slug
    still holds a process, deleting its tree creates an orphan holding a port (the reason
    `ProtoHost.purge` calls `stop` first).
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    _seed_project("del-gate", sessions, root)
    _seed_prototype(_proto_wiring, "del-gate", "demo", "tok-gate")
    _proto_wiring["host"].purge_exc = RuntimeError("rmtree denied")

    r = client.delete("/projects/del-gate")

    assert r.status_code == 500
    assert _proto_wiring["host"].purged_projects == [], (
        "슬러그가 실패했는데 부모를 지웠다")
    # S3 and the registry must remain so that a retry is meaningful.
    assert app_module.registry.is_registered("del-gate")
    assert any(k.startswith("del-gate/") for k in root.blobs)
    app_module.registry.remove("del-gate")
