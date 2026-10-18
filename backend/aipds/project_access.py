# backend/aipds/project_access.py — who can do what to which project.
#
# Each project has one owner and a list of viewing members. There are three rules:
#
#   admin   reads and writes every project (writing is needed for cleanup and granting ownership).
#   owner   reads and writes their own projects.
#   member  only reads. The purpose of membership is viewing, not collaboration — things that move
#           the project forward, like chat, approvals, and builds, are done by the single owner.
#
# Read and write are split **by HTTP method**: GET and HEAD are reads, the rest are writes. This app's
# GETs do not change state (even the hosting status query does not mint a token — host_status in
# routes/prototypes). The one exception split by content rather than method is export (require_project_owner).
#
# Identity is the Cognito **username** — not the email. In this pool, the username is the email local
# part (auth/cognito.username_for_email), and that is all the access token carries. Why not sub:
# if an admin deletes a user and re-invites them with the same email, the sub changes but the username stays the same.
#
# A project one cannot access is a **404** (the same response as a nonexistent project). A member's write is a 403 —
# the member already knows the project exists.
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import Depends, HTTPException, Request

from aipds import error_codes as ec
from aipds.auth.cognito import username_for_email
from aipds.auth.deps import require_user
from aipds.auth.models import Principal

#: The seed admin (SEED_ADMIN_EMAIL in infra/lib/auth-client-config.ts). Projects with no recorded owner
#: and projects of deleted users are attributed here. So this account cannot be deleted or demoted
#: (routes/admin_users). A test checks that it matches the infra-side value — the reason it is not taken
#: from env is that changing HostingStack's user-data replaces the instance.
SEED_ADMIN_EMAIL = "admin@aipds.local"
SEED_ADMIN = username_for_email(SEED_ADMIN_EMAIL)

#: Member cap per project. It is a list that fits in one manifest, and it is for viewing, so it is generous.
MAX_MEMBERS = 50

Access = Literal["admin", "owner", "member"]
_READ_METHODS = frozenset({"GET", "HEAD"})


def normalize(username: str) -> str:
    """Username for comparison and storage. Cognito interprets usernames case-insensitively —
    routes/admin_users._guard_privilege_removal compares with casefold for the same reason."""
    return username.strip().casefold()


def username_of(value: str) -> str:
    """Member/owner input (email or username) → normalized username.

    If it is an email, it is converted with the same rule the invite used to create the username. People
    know their colleagues by email, and what is stored is the username. ValueError if the value is unusable."""
    value = value.strip()
    if "@" in value:
        return normalize(username_for_email(value))
    # Non-email input must pass the same character rule too — a value outside that rule cannot be
    # the username of any Cognito account.
    if not value or username_for_email(value) != value.lower():
        raise ValueError(f"not a username: {value!r}")
    return normalize(value)


def clean_access(owner: str, members: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Normalize, remove the owner from the members, and drop duplicates (keeping first-seen order).
    The registry and the manifest use the same rule."""
    owner = normalize(owner)
    seen: list[str] = []
    for m in (normalize(m) for m in members):
        if m and m != owner and m not in seen:
            seen.append(m)
    return owner, tuple(seen)


def same_user_key(value: str) -> str:
    """User identifier from the path → comparison key. The Cognito Admin API also accepts the email
    alias in the username slot, so even if it comes in as an email, the key of the same account must come out.
    Values outside the rule are only normalized (no such username is recorded in any project)."""
    try:
        return username_of(value)
    except ValueError:
        return normalize(value)


def access_of(principal: Principal, project_id: str) -> Access | None:
    import aipds.app as app_module
    if principal.role == "admin":
        return "admin"
    me = normalize(principal.username)
    if app_module.registry.get_owner(project_id) == me:
        return "owner"
    if me in app_module.registry.get_members(project_id):
        return "member"
    return None


def can_write(access: Access | None) -> bool:
    return access in ("admin", "owner")


_NOT_FOUND = HTTPException(status_code=404, detail="unknown project")


async def require_project_access(
        request: Request, principal: Principal = Depends(require_user)) -> None:
    """Gate for routes that have `{pid}`. Attached per router (_PROJECT_AUTH in app.py).

    Unregistered pids are let through — the route returns its usual 404. Blocking here would split
    the "nonexistent project" response into each route's own and this gate's."""
    import aipds.app as app_module
    pid = request.path_params.get("pid")
    if pid is None or not app_module.registry.is_registered(pid):
        return
    access = access_of(principal, pid)
    if access is None:
        raise _NOT_FOUND
    if request.method not in _READ_METHODS and not can_write(access):
        raise HTTPException(status_code=403, detail=ec.PROJECT_READ_ONLY)


async def require_project_owner(
        request: Request, principal: Principal = Depends(require_user)) -> None:
    """Owner/admin only, regardless of method. Used by export — importing a bundle under a new id makes
    the importer the owner of that copy, so opening it to members would turn viewing rights into ownership."""
    import aipds.app as app_module
    pid = request.path_params.get("pid")
    if pid is None or not app_module.registry.is_registered(pid):
        return
    access = access_of(principal, pid)
    if access is None:
        raise _NOT_FOUND
    if not can_write(access):
        raise HTTPException(status_code=403, detail=ec.PROJECT_READ_ONLY)


def visible_ids(principal: Principal) -> list[str]:
    """In list order (registry.list_ids) as is, only those this requester can see."""
    import aipds.app as app_module
    ids = app_module.registry.list_ids()
    if principal.role == "admin":
        return ids
    return [pid for pid in ids if access_of(principal, pid) is not None]


# ---- Mutations ----

_locks: dict[str, asyncio.Lock] = {}


def access_lock(project_id: str) -> asyncio.Lock:
    """Serializes owner/members changes. Member editing, ownership grants, and user deletion all
    read-modify-write the same manifest — if they overlap, one side's change is lost. The backend is a single process."""
    return _locks.setdefault(project_id, asyncio.Lock())


async def set_access(project_id: str, owner: str, members: tuple[str, ...]) -> None:
    """Write the manifest first, then align the registry. If the manifest write fails, the exception
    propagates and the registry stays as is — we do not show on screen a change that would revert after a restart.
    The caller must hold access_lock."""
    import aipds.app as app_module
    from aipds.project_store import write_manifest_access
    owner, members = clean_access(owner, members)
    if app_module.durable_projects_enabled():
        await write_manifest_access(app_module.projects_root_s3_factory(),
                                    project_id, owner, members)
    app_module.registry.set_access(project_id, owner, members)


async def release_user(username: str) -> None:
    """Hand the deleted user's projects over to the seed admin and remove them from member lists.

    The previous owner is not kept as a member — that account is about to disappear, and someone
    re-invited with the same email must not inherit the old membership. It is idempotent per project,
    so if it fails midway, the same request can simply be sent again."""
    import aipds.app as app_module
    me = same_user_key(username)
    registry = app_module.registry
    for pid in registry.list_ids():
        if registry.get_owner(pid) != me and me not in registry.get_members(pid):
            continue
        async with access_lock(pid):
            if not registry.is_registered(pid):
                continue  # deleted while waiting
            owner = registry.get_owner(pid) or SEED_ADMIN
            members = registry.get_members(pid)
            if owner == me:
                owner = SEED_ADMIN
            await set_access(pid, owner, tuple(m for m in members if m != me))
