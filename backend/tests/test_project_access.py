# backend/tests/test_project_access.py — owner, viewer members, admin (aipds/project_access.py).
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import fastapi.routing as fastapi_routing
import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds import project_access
from aipds.auth.deps import require_admin, require_user
from aipds.auth.models import Principal
from aipds.project_store import restore_projects, write_manifest, write_manifest_access
from tests.fakes.in_memory_s3 import FakeS3Store
from tests.test_auth_route_coverage import PUBLIC_PATHS

OWNER = Principal(username="kim", sub="s-kim", role="pm")
MEMBER = Principal(username="lee", sub="s-lee", role="pm")
STRANGER = Principal(username="park", sub="s-park", role="pm")
ADMIN = Principal(username="ops", sub="s-ops", role="admin")


@pytest.fixture()
def as_user():
    """Swaps in the requester. require_admin also goes through require_user, so pm gets
    a 403 there."""
    current: dict[str, Principal] = {}

    def _set(p: Principal) -> TestClient:
        current["p"] = p
        app_module.app.dependency_overrides[require_user] = lambda: current["p"]
        return TestClient(app_module.app)

    yield _set
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def project(monkeypatch):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    app_module.registry.register("pa-1", "기획", owner="kim", members=("lee",))
    return "pa-1"


# ---- Route coverage ----

def _routes() -> list[Any]:
    out: list[Any] = []
    for r in app_module.app.routes:
        if isinstance(r, fastapi_routing._IncludedRouter):
            out.extend(r.effective_candidates())
    return out


def _calls(route: Any) -> set:
    return {getattr(d, "call", None) for d in route.dependant.dependencies}


def test_every_project_route_checks_project_access():
    # If a `{pid}` route is missing the access gate, anyone logged in can reach that
    # project. This catches the mistake of attaching a new router with _AUTH. For
    # /admin, require_admin is the stronger gate.
    missing = [r.path for r in _routes()
               if "{pid}" in r.path and r.path not in PUBLIC_PATHS
               and project_access.require_project_access not in _calls(r)
               and require_admin not in _calls(r)]
    assert missing == [], f"프로젝트 접근 게이트가 없다: {missing}"


def test_export_is_owner_only():
    export = [r for r in _routes() if r.path == "/projects/{pid}/export"]
    assert export and project_access.require_project_owner in _calls(export[0])


def _fill(path: str) -> str:
    return (path.replace("{pid}", "pa-1").replace("{slug}", "s")
            .replace("{name:path}", "q.md").replace("{path:path}", "x.md"))


def _project_routes() -> list[tuple[str, str]]:
    return [(m, r.path) for r in _routes() for m in r.methods
            if "{pid}" in r.path and r.path not in PUBLIC_PATHS
            and not r.path.startswith("/admin")]


def test_a_stranger_gets_404_everywhere(project, as_user):
    client = as_user(STRANGER)
    wrong = [(m, p) for m, p in _project_routes()
             if client.request(m, _fill(p)).status_code != 404]
    assert wrong == []
    # Same response as for a nonexistent project — the existence of a project you cannot
    # access is not revealed.
    assert client.get("/projects/pa-1").json() == {"detail": "unknown project"}


def test_a_member_cannot_write_anywhere(project, as_user):
    client = as_user(MEMBER)
    wrong = []
    for m, p in _project_routes():
        if m in ("GET", "HEAD") and not p.endswith("/export"):
            continue
        r = client.request(m, _fill(p))
        if r.status_code != 403 or r.json()["detail"] != "project_read_only":
            wrong.append((m, p, r.status_code))
    assert wrong == []


def test_a_member_reads(project, as_user):
    body = as_user(MEMBER).get("/projects/pa-1").json()
    assert body["access"] == "member" and body["owner"] == "kim"
    # Viewer members are not given the list of other viewers.
    assert "members" not in body


def test_owner_and_admin_see_the_members(project, as_user):
    assert as_user(OWNER).get("/projects/pa-1").json()["members"] == ["lee"]
    body = as_user(ADMIN).get("/projects/pa-1").json()
    assert body["access"] == "admin" and body["members"] == ["lee"]


def test_username_comparison_ignores_case(project, as_user):
    shouting = Principal(username="KIM", sub="s-kim", role="pm")
    assert as_user(shouting).get("/projects/pa-1").json()["access"] == "owner"


# ---- List ----

def test_list_shows_only_visible_projects_and_counts_them(monkeypatch, as_user):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    app_module.registry.register("pl-1", owner="kim", created_at="2026-10-01")
    app_module.registry.register("pl-2", owner="park", members=("kim",), created_at="2026-10-02")
    app_module.registry.register("pl-3", owner="park", created_at="2026-10-03")
    body = as_user(OWNER).get("/projects?size=50").json()
    mine = {p["project_id"]: p["access"] for p in body["projects"]
            if p["project_id"].startswith("pl-")}
    assert mine == {"pl-1": "owner", "pl-2": "member"}
    # total is the count after filtering — other people's projects do not leak into the
    # page count.
    assert body["total"] == len([p for p in app_module.registry.list_ids()
                                 if project_access.access_of(OWNER, p)])
    admin_ids = {p["project_id"] for p in as_user(ADMIN).get("/projects?size=50").json()["projects"]}
    assert {"pl-1", "pl-2", "pl-3"} <= admin_ids


def test_the_creator_owns_a_new_project(monkeypatch, as_user):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    body = as_user(STRANGER).post("/projects", json={"project_id": "pc-1"}).json()
    assert body["owner"] == "park" and body["access"] == "owner"


def test_the_creator_is_written_to_the_manifest(monkeypatch, as_user):
    fake = FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "b")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    assert as_user(STRANGER).post("/projects", json={"project_id": "pc-2"}).status_code == 200
    d = json.loads(fake.blobs["pc-2/project.json"])
    assert d["owner"] == "park" and d["members"] == []


# ---- Members ----

def test_owner_sets_members_by_email_or_username(project, as_user):
    r = as_user(OWNER).put("/projects/pa-1/members",
                           json={"members": ["Choi@Example.com", "jung", "kim", "jung"]})
    assert r.status_code == 200
    # Emails are converted to usernames, and the owner and duplicates are dropped.
    assert r.json()["members"] == ["choi", "jung"]
    assert app_module.registry.get_members("pa-1") == ("choi", "jung")


def test_a_value_that_cannot_be_a_username_is_400(project, as_user):
    r = as_user(OWNER).put("/projects/pa-1/members", json={"members": ["not a user!"]})
    assert r.status_code == 400 and r.json()["detail"] == "member_invalid"


def test_members_have_an_upper_bound(project, as_user):
    many = [f"u{i}" for i in range(project_access.MAX_MEMBERS + 1)]
    r = as_user(OWNER).put("/projects/pa-1/members", json={"members": many})
    assert r.status_code == 400


def test_member_change_writes_the_manifest_first(monkeypatch, as_user):
    fake = FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "b")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    app_module.registry.register("pm-1", owner="kim")
    fake.blobs["pm-1/project.json"] = json.dumps(
        {"project_id": "pm-1", "name": "n", "language": "en", "owner": "kim", "members": []})
    assert as_user(OWNER).put("/projects/pm-1/members",
                              json={"members": ["lee"]}).status_code == 200
    d = json.loads(fake.blobs["pm-1/project.json"])
    # The remaining fields are exactly as read.
    assert d == {"project_id": "pm-1", "name": "n", "language": "en",
                 "owner": "kim", "members": ["lee"]}


def test_a_failed_manifest_write_leaves_the_members_as_they_were(monkeypatch, as_user):
    fake = FakeS3Store()  # no project.json → write fails
    monkeypatch.setenv("AIPDS_S3_BUCKET", "b")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    app_module.registry.register("pm-2", owner="kim", members=("lee",))
    r = as_user(OWNER).put("/projects/pm-2/members", json={"members": []})
    assert r.status_code == 500
    assert app_module.registry.get_members("pm-2") == ("lee",)


@pytest.mark.asyncio
async def test_delete_waits_for_an_access_change_on_the_same_project(monkeypatch):
    # If a deletion slips in while an access change is reading, modifying, and writing
    # the manifest, the deleted project.json comes back to life.
    import asyncio
    from aipds.routes import projects as projects_routes
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    order: list[str] = []

    async def _fake_delete(pid):
        order.append("delete")
        return {"deleted": True}

    monkeypatch.setattr(projects_routes, "_delete_project", _fake_delete)
    lock = project_access.access_lock("pl-lock")
    await lock.acquire()
    task = asyncio.create_task(projects_routes.delete_project("pl-lock"))
    await asyncio.sleep(0)
    order.append("access change done")
    lock.release()
    await task
    assert order == ["access change done", "delete"]


# ---- Granting ownership (admin) ----

class _Cognito:
    def __init__(self, users: dict[str, tuple[bool, list[str]]]):
        self.users = users
        self.deleted: list[str] = []

    def is_enabled(self, username):
        from aipds.auth.cognito import CognitoError
        if username not in self.users:
            raise CognitoError("UserNotFoundException", username)
        return self.users[username][0]

    def groups_of(self, username):
        from aipds.auth.cognito import CognitoError
        if username not in self.users:
            raise CognitoError("UserNotFoundException", username)
        return self.users[username][1]

    def admin_count(self):
        return sum(1 for _, g in self.users.values() if "admin" in g)

    def delete_user(self, username):
        self.deleted.append(username)
        self.users.pop(username, None)

    def set_group(self, username, role):
        self.users[username] = (self.users[username][0], [role])


@pytest.fixture()
def cognito(monkeypatch):
    fake = _Cognito({"admin": (True, ["admin"]), "ops": (True, ["admin"]),
                     "kim": (True, ["pm"]), "lee": (True, ["pm"]),
                     "park": (True, ["pm"]), "off": (False, ["pm"]), "norole": (True, [])})
    monkeypatch.setattr(app_module, "cognito_admin", lambda: fake)
    monkeypatch.setattr(app_module, "cognito_config",
                        lambda: {"region": "r", "user_pool_id": "p", "client_id": "c"})
    return fake


def test_admin_assigns_an_owner_and_the_previous_owner_stays_as_a_member(
        project, as_user, cognito):
    r = as_user(ADMIN).put("/admin/projects/pa-1/owner", json={"owner": "park@example.com"})
    assert r.status_code == 200
    assert app_module.registry.get_owner("pa-1") == "park"
    assert app_module.registry.get_members("pa-1") == ("lee", "kim")


def test_assigning_from_the_seed_admin_does_not_keep_it_as_a_member(monkeypatch, as_user, cognito):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    app_module.registry.register("pa-2")  # no owner record → seed admin
    assert as_user(ADMIN).put("/admin/projects/pa-2/owner",
                              json={"owner": "kim"}).status_code == 200
    assert app_module.registry.get_owner("pa-2") == "kim"
    assert app_module.registry.get_members("pa-2") == ()


def test_the_new_owner_leaves_the_member_list(project, as_user, cognito):
    as_user(ADMIN).put("/admin/projects/pa-1/owner", json={"owner": "lee"})
    assert app_module.registry.get_owner("pa-1") == "lee"
    assert app_module.registry.get_members("pa-1") == ("kim",)


@pytest.mark.parametrize("who,status,detail", [
    ("off", 400, "owner_not_assignable"),
    ("norole", 400, "owner_not_assignable"),
    ("ghost", 404, "user_not_found"),
])
def test_ownership_goes_only_to_an_account_that_can_sign_in(
        project, as_user, cognito, who, status, detail):
    r = as_user(ADMIN).put("/admin/projects/pa-1/owner", json={"owner": who})
    assert (r.status_code, r.json()["detail"]) == (status, detail)
    assert app_module.registry.get_owner("pa-1") == "kim"


def test_a_pm_cannot_assign_owners(project, as_user, cognito):
    assert as_user(OWNER).put("/admin/projects/pa-1/owner",
                              json={"owner": "park"}).status_code == 403


# ---- User deletion ----

def test_deleting_a_user_hands_their_projects_to_the_seed_admin(monkeypatch, as_user, cognito):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    app_module.registry.register("pd-1", owner="kim", members=("lee",))
    app_module.registry.register("pd-2", owner="park", members=("kim", "lee"))
    assert as_user(ADMIN).delete("/admin/users/kim").status_code == 204
    assert cognito.deleted == ["kim"]
    # Ownership goes to the seed admin, and the deleted person remains nowhere — someone
    # re-invited with the same email must not inherit the old memberships.
    assert app_module.registry.get_owner("pd-1") == "admin"
    assert app_module.registry.get_members("pd-1") == ("lee",)
    assert app_module.registry.get_members("pd-2") == ("lee",)


def test_handover_happens_before_the_account_is_deleted(monkeypatch, as_user, cognito):
    fake = FakeS3Store()  # no manifest → transfer fails
    monkeypatch.setenv("AIPDS_S3_BUCKET", "b")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    app_module.registry.register("pd-3", owner="kim")
    r = as_user(ADMIN).delete("/admin/users/kim")
    assert r.status_code == 500
    # The account is still there — just send the same request again.
    assert cognito.deleted == [] and "kim" in cognito.users


def test_the_seed_admin_cannot_be_deleted(as_user, cognito):
    r = as_user(ADMIN).delete("/admin/users/admin")
    assert (r.status_code, r.json()["detail"]) == (400, "seed_admin_protected")
    assert cognito.deleted == []


@pytest.mark.parametrize("target", ["admin", "ADMIN", " admin ", "admin@aipds.local",
                                    "Admin@AIPDS.local"])
def test_the_seed_admin_guard_sees_every_spelling_of_the_account(target):
    # The Cognito Admin API resolves usernames case-insensitively and also accepts email
    # aliases.
    from fastapi import HTTPException
    from aipds.routes.admin_users import _guard_seed_admin
    with pytest.raises(HTTPException):
        _guard_seed_admin(target)


def test_the_seed_admin_cannot_be_demoted(as_user, cognito):
    r = as_user(ADMIN).put("/admin/users/admin/role", json={"role": "pm"})
    assert (r.status_code, r.json()["detail"]) == (400, "seed_admin_protected")
    assert cognito.users["admin"][1] == ["admin"]


# ---- Manifest ----

@pytest.mark.asyncio
async def test_a_manifest_without_an_owner_belongs_to_the_seed_admin():
    root = FakeS3Store()
    root.blobs["old/project.json"] = json.dumps({"project_id": "old", "name": "x"})
    await write_manifest(root, "new", None, owner="kim", members=("lee",))
    restored = {p.project_id: p for p in await restore_projects(root)}
    assert (restored["old"].owner, restored["old"].members) == (None, ())
    assert (restored["new"].owner, restored["new"].members) == ("kim", ("lee",))
    app_module.registry.register("old", owner=restored["old"].owner)
    assert app_module.registry.get_owner("old") == project_access.SEED_ADMIN


@pytest.mark.asyncio
async def test_write_manifest_access_needs_an_existing_manifest():
    with pytest.raises(Exception):
        await write_manifest_access(FakeS3Store(), "none", "kim", ())


def test_the_seed_admin_matches_the_infra_seed_account():
    # Creating the seed account is infra's job. If the two values diverge, the account
    # ends up with nothing to be attributed to.
    src = (Path(__file__).resolve().parents[2]
           / "infra/lib/auth-client-config.ts").read_text(encoding="utf-8")
    m = re.search(r"SEED_ADMIN_EMAIL\s*=\s*'([^']+)'", src)
    assert m and m.group(1) == project_access.SEED_ADMIN_EMAIL
    assert project_access.SEED_ADMIN == "admin"
