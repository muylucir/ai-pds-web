# backend/pathfinder/auth/cognito.py
#
# Cognito Admin* API 래퍼. boto3 호출을 라우트에서 분리하는 이유는 s3store.py와
# 같다: 라우트는 정책(마지막 관리자 보호, 부분 실패 롤백)만 다루고, 이 파일은
# Stubber로 독립 검증된다.
#
# 이 풀은 AliasAttributes(email)이므로 Username을 호출자가 정한다 — 우리는
# 이메일을 그대로 Username으로 쓴다. 그래서 모든 Admin* 호출이 이메일로 결정적이다.
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from botocore.exceptions import ClientError

from pathfinder.auth.models import ROLE_ADMIN, ROLE_PM, Role

_log = logging.getLogger(__name__)

# 한 번에 가져오는 사용자 수. Cognito의 상한은 60이다.
_PAGE = 60

# 우리가 관리하는 역할 그룹. 이 밖의 그룹은 건드리지 않는다.
_ROLE_GROUPS = (ROLE_ADMIN, ROLE_PM)

# 임시 비밀번호 문자군. 풀 정책(8자+ 대/소/숫자/기호)을 만족시키기 위해 각 군에서
# 최소 1자를 보장한다. 혼동하기 쉬운 문자(0/O, 1/l/I)는 제외했다 — 임시 비밀번호는
# 사람이 메신저로 옮겨 적는 값이다.
_LOWER = "abcdefghijkmnopqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_SYMBOLS = "!@#$%^&*_-+=?"


class CognitoError(Exception):
    """Cognito가 거부했다. `code`는 원문 오류 코드(라우트가 상태코드로 번역한다)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class ManagedUser:
    username: str      # Admin* API 호출에 쓰는 값
    email: str         # 화면에 보여주는 값
    role: Role | None  # 그룹 미배정(반쯤 만들어진 계정)이면 None
    status: str        # CONFIRMED / FORCE_CHANGE_PASSWORD / ...
    enabled: bool
    created_at: str    # ISO8601


def generate_temp_password(length: int = 16) -> str:
    """정책을 만족하는 임시 비밀번호.

    각 문자군에서 1자를 먼저 고른 뒤 나머지를 채우고 섞는다. 무작위 문자열을
    뽑아 정책 통과를 기대하는 방식은 드물게 실패하고, 그 실패는 사용자가 이미
    생성된 뒤에 InvalidPasswordException으로 나타난다.
    """
    pools = (_LOWER, _UPPER, _DIGITS, _SYMBOLS)
    chars = [secrets.choice(p) for p in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(pools))]
    # secrets 기반 Fisher-Yates — random.shuffle은 암호학적으로 안전하지 않다.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


class CognitoAdmin:
    def __init__(self, client, user_pool_id: str) -> None:
        self._c = client
        self._pool = user_pool_id

    # ---- 내부 ----

    def _call(self, name: str, **params):
        try:
            return getattr(self._c, name)(UserPoolId=self._pool, **params)
        except ClientError as exc:
            err = exc.response.get("Error", {})
            code = err.get("Code", "Unknown")
            raise CognitoError(code, err.get("Message", str(exc))) from exc

    @staticmethod
    def _email_of(raw: dict) -> str:
        for attr in raw.get("Attributes", []):
            if attr.get("Name") == "email":
                return attr.get("Value", "")
        # 이메일 속성이 없는 계정(수동 생성 등)도 화면에서 식별 가능해야 한다.
        return raw.get("Username", "")

    @staticmethod
    def _role_of(groups: list[str]) -> Role | None:
        # verifier와 같은 우선순위: 두 그룹에 다 속하면 admin으로 본다.
        if ROLE_ADMIN in groups:
            return ROLE_ADMIN
        if ROLE_PM in groups:
            return ROLE_PM
        return None

    # ---- 조회 ----

    def groups_of(self, username: str) -> list[str]:
        resp = self._call("admin_list_groups_for_user", Username=username)
        return [g["GroupName"] for g in resp.get("Groups", [])]

    def list_users(self) -> list[ManagedUser]:
        users: list[ManagedUser] = []
        token: str | None = None
        while True:
            params = {"Limit": _PAGE}
            if token:
                params["PaginationToken"] = token
            resp = self._call("list_users", **params)
            for raw in resp.get("Users", []):
                username = raw.get("Username", "")
                created = raw.get("UserCreateDate")
                users.append(ManagedUser(
                    username=username,
                    email=self._email_of(raw),
                    role=self._role_of(self.groups_of(username)),
                    status=raw.get("UserStatus", ""),
                    enabled=bool(raw.get("Enabled", True)),
                    created_at=created.isoformat() if created else "",
                ))
            token = resp.get("PaginationToken")
            if not token:
                return users

    def admin_count(self) -> int:
        """admin 그룹 멤버 수 — 마지막 관리자 보호의 입력."""
        total = 0
        token: str | None = None
        while True:
            params = {"GroupName": ROLE_ADMIN, "Limit": _PAGE}
            if token:
                params["NextToken"] = token
            resp = self._call("list_users_in_group", **params)
            total += len(resp.get("Users", []))
            token = resp.get("NextToken")
            if not token:
                return total

    # ---- 변경 ----

    def create_user(self, email: str) -> str:
        """사용자를 만들고 Username을 반환한다.

        MessageAction=SUPPRESS: 이 앱은 메일을 보내지 않는다(초대는 관리 페이지가
        임시 비밀번호를 화면에 1회 보여준다).
        email_verified=true: 선택이 아니라 alias(email) 사인인의 조건이다.
        """
        resp = self._call(
            "admin_create_user",
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email},
                            {"Name": "email_verified", "Value": "true"}],
        )
        return resp.get("User", {}).get("Username", email)

    def set_temp_password(self, username: str, password: str) -> None:
        """임시 비밀번호. Permanent=False라 첫 로그인에서 사용자가 직접 바꾼다."""
        self._call("admin_set_user_password", Username=username,
                   Password=password, Permanent=False)

    def set_group(self, username: str, role: Role) -> None:
        """역할을 교체한다 — 추가가 아니라 교체다.

        기존 역할 그룹을 지우지 않으면 사용자가 admin과 pm에 동시에 속해
        강등이 무효가 된다(verifier가 admin을 우선한다). admin/pm 밖의 그룹은
        우리 관심사가 아니므로 건드리지 않는다.
        """
        for existing in self.groups_of(username):
            if existing in _ROLE_GROUPS and existing != role:
                self._call("admin_remove_user_from_group", Username=username,
                           GroupName=existing)
        self._call("admin_add_user_to_group", Username=username, GroupName=role)

    def set_enabled(self, username: str, enabled: bool) -> None:
        action = "admin_enable_user" if enabled else "admin_disable_user"
        self._call(action, Username=username)

    def delete_user(self, username: str) -> None:
        self._call("admin_delete_user", Username=username)
