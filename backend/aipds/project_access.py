# backend/aipds/project_access.py — 누가 어느 프로젝트에 무엇을 할 수 있는가.
#
# 프로젝트마다 소유자 한 명과 열람 멤버 목록이 있다. 규칙은 셋이다:
#
#   admin   모든 프로젝트를 읽고 쓴다(정리·소유권 부여를 하려면 쓰기가 필요하다).
#   owner   자기 프로젝트를 읽고 쓴다.
#   member  읽기만 한다. 멤버의 목적은 열람이지 협업이 아니다 — 대화·승인·빌드처럼
#           프로젝트를 움직이는 것은 소유자 한 사람이 한다.
#
# 읽기와 쓰기는 **HTTP 메서드로** 가른다: GET·HEAD는 읽기, 나머지는 쓰기. 이 앱의 GET은
# 상태를 바꾸지 않는다(호스팅 상태 조회도 토큰을 만들지 않는다 — routes/prototypes의
# host_status). 메서드가 아닌 내용으로 가르는 예외는 내보내기 하나다(require_project_owner).
#
# 신원은 Cognito **username**이다 — 이메일이 아니다. 이 풀의 username은 이메일 로컬파트이고
# (auth/cognito.username_for_email) access 토큰에는 그것만 실린다. sub를 쓰지 않는 이유:
# 관리자가 사용자를 지웠다가 같은 이메일로 다시 초대하면 sub는 바뀌고 username은 같다.
#
# 접근할 수 없는 프로젝트는 **404**다(없는 프로젝트와 같은 응답). 멤버의 쓰기는 403이다 —
# 멤버는 프로젝트가 있다는 것을 이미 안다.
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import Depends, HTTPException, Request

from aipds import error_codes as ec
from aipds.auth.cognito import username_for_email
from aipds.auth.deps import require_user
from aipds.auth.models import Principal

#: 시드 관리자(infra/lib/auth-client-config.ts의 SEED_ADMIN_EMAIL). 소유자가 기록되지 않은
#: 프로젝트와 삭제된 사용자의 프로젝트가 여기로 귀속된다. 그래서 이 계정은 삭제·강등할 수
#: 없다(routes/admin_users). 인프라 쪽 값과 같은지는 테스트가 확인한다 — env로 받지 않는
#: 이유는 HostingStack의 user-data를 바꾸면 인스턴스가 교체되기 때문이다.
SEED_ADMIN_EMAIL = "admin@aipds.local"
SEED_ADMIN = username_for_email(SEED_ADMIN_EMAIL)

#: 한 프로젝트의 멤버 상한. 매니페스트 하나에 들어가는 목록이고, 열람용이므로 넉넉하다.
MAX_MEMBERS = 50

Access = Literal["admin", "owner", "member"]
_READ_METHODS = frozenset({"GET", "HEAD"})


def normalize(username: str) -> str:
    """비교·저장용 username. Cognito는 username을 대소문자 구분 없이 해석한다 —
    routes/admin_users._guard_privilege_removal이 같은 이유로 casefold로 비교한다."""
    return username.strip().casefold()


def username_of(value: str) -> str:
    """멤버·소유자 입력(이메일 또는 username) → 정규화된 username.

    이메일이면 초대가 username을 만든 것과 같은 규칙으로 바꾼다. 사람은 동료를 이메일로
    알고, 저장되는 것은 username이다. 쓸 수 없는 값이면 ValueError."""
    value = value.strip()
    if "@" in value:
        return normalize(username_for_email(value))
    # 이메일이 아닌 입력도 같은 문자 규칙을 통과해야 한다 — 그 규칙 밖의 값은 어떤
    # Cognito 계정의 username도 될 수 없다.
    if not value or username_for_email(value) != value.lower():
        raise ValueError(f"not a username: {value!r}")
    return normalize(value)


def clean_access(owner: str, members: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """정규화하고, 멤버에서 소유자를 빼고, 중복을 걷는다(처음 나온 순서 유지).
    레지스트리와 매니페스트가 같은 규칙을 쓴다."""
    owner = normalize(owner)
    seen: list[str] = []
    for m in (normalize(m) for m in members):
        if m and m != owner and m not in seen:
            seen.append(m)
    return owner, tuple(seen)


def same_user_key(value: str) -> str:
    """경로로 들어온 사용자 식별자 → 비교 키. Cognito Admin API는 username 자리에 이메일
    alias도 받으므로, 이메일로 들어와도 같은 계정의 키가 나와야 한다. 규칙 밖의 값은
    정규화만 한다(그런 username은 어떤 프로젝트에도 기록돼 있지 않다)."""
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
    """`{pid}`가 있는 라우트의 게이트. 라우터 단위로 붙인다(app.py의 _PROJECT_AUTH).

    등록되지 않은 pid는 통과시킨다 — 라우트가 평소대로 404를 낸다. 여기서 막으면
    "없는 프로젝트"의 응답이 라우트마다의 것과 이 게이트의 것으로 갈린다."""
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
    """메서드와 상관없이 소유자·관리자만. 내보내기가 쓴다 — 번들을 새 id로 가져오면
    가져온 사람이 그 사본의 소유자가 되므로, 멤버에게 열면 열람 권한이 소유권이 된다."""
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
    """목록 순서(registry.list_ids) 그대로, 이 요청자가 볼 수 있는 것만."""
    import aipds.app as app_module
    ids = app_module.registry.list_ids()
    if principal.role == "admin":
        return ids
    return [pid for pid in ids if access_of(principal, pid) is not None]


# ---- 변경 ----

_locks: dict[str, asyncio.Lock] = {}


def access_lock(project_id: str) -> asyncio.Lock:
    """owner·members 변경의 직렬화. 멤버 편집·소유권 부여·사용자 삭제가 같은 매니페스트를
    읽고-고쳐-쓴다 — 겹치면 한쪽 변경이 사라진다. 백엔드는 한 프로세스다."""
    return _locks.setdefault(project_id, asyncio.Lock())


async def set_access(project_id: str, owner: str, members: tuple[str, ...]) -> None:
    """매니페스트를 먼저 쓰고 레지스트리를 맞춘다. 매니페스트 쓰기가 실패하면 예외가
    전파되고 레지스트리는 그대로다 — 재시작 뒤에 되돌아가는 변경을 화면에 보이지 않는다.
    호출부가 access_lock을 쥐고 있어야 한다."""
    import aipds.app as app_module
    from aipds.project_store import write_manifest_access
    owner, members = clean_access(owner, members)
    if app_module.durable_projects_enabled():
        await write_manifest_access(app_module.projects_root_s3_factory(),
                                    project_id, owner, members)
    app_module.registry.set_access(project_id, owner, members)


async def release_user(username: str) -> None:
    """삭제되는 사용자의 프로젝트를 시드 관리자에게 넘기고, 멤버 목록에서 뺀다.

    이전 소유자를 멤버로 남기지 않는다 — 그 계정은 곧 사라지고, 같은 이메일로 다시
    초대된 사람이 예전 멤버십을 물려받으면 안 된다. 프로젝트마다 멱등이므로 중간에
    실패하면 같은 요청을 다시 보내면 된다."""
    import aipds.app as app_module
    me = same_user_key(username)
    registry = app_module.registry
    for pid in registry.list_ids():
        if registry.get_owner(pid) != me and me not in registry.get_members(pid):
            continue
        async with access_lock(pid):
            if not registry.is_registered(pid):
                continue  # 기다리는 사이 삭제됐다
            owner = registry.get_owner(pid) or SEED_ADMIN
            members = registry.get_members(pid)
            if owner == me:
                owner = SEED_ADMIN
            await set_access(pid, owner, tuple(m for m in members if m != me))
