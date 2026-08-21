# backend/aipds/auth/cognito.py
#
# Cognito Admin* API 래퍼. boto3 호출을 라우트에서 분리하는 이유는 s3store.py와
# 같다: 라우트는 정책(마지막 관리자 보호, 부분 실패 롤백)만 다루고, 이 파일은
# Stubber로 독립 검증된다.
#
# 이 풀은 AliasAttributes(email)이므로 Username을 호출자가 정한다 — 우리는
# 이메일의 로컬파트를 Username으로 쓴다(username_for_email 참조). 그래서 모든
# Admin* 호출이 이메일로부터 결정적이다.
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

from aipds.auth.models import ROLE_ADMIN, ROLE_PM, Role

_log = logging.getLogger(__name__)

# 한 번에 가져오는 사용자 수. Cognito의 상한은 60이다.
_PAGE = 60

# 우리가 관리하는 역할 그룹. 이 밖의 그룹은 건드리지 않는다.
_ROLE_GROUPS = (ROLE_ADMIN, ROLE_PM)

# Cognito Username이 받아주지 않는 문자를 '-'로 치환한다. Username에 허용되는
# 문자는 제한적이라(예: 태그 주소의 '+') 로컬파트를 그대로 넘기면
# InvalidParameterException이 난다.
_USERNAME_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def username_for_email(email: str) -> str:
    """이메일 → Cognito Username(로컬파트).

    이 풀은 AliasAttributes=[email]이다. Cognito는 그 설정에서 **이메일 형식
    Username을 거부한다** ("Username cannot be of email format, since user pool
    is configured for email alias") — 이메일이 alias로 예약되어 있어 username과
    충돌하기 때문이다. 실측: 이 규칙을 몰라 시드 계정 생성이 스택 롤백을 냈다.

    그래서 '@' 앞부분만 Username으로 쓴다. 사용자는 어느 쪽이든 이메일로
    로그인한다(email alias가 그 일을 한다).

    ⚠️ 로컬파트가 같고 도메인만 다른 두 계정(kim@a.com / kim@b.com)은 같은
    Username으로 충돌한다 — 두 번째 초대가 UsernameExistsException으로 실패한다.
    워크숍 규모(단일 도메인)에서 감수한 트레이드오프다. 다중 도메인을 받아야
    하면 도메인까지 포함한 규칙으로 바꿔야 하고, 그때는 infra/lib/seed-users.ts의
    동일 규칙도 함께 고쳐야 한다(두 곳이 어긋나면 시드가 비결정적이 된다).
    """
    local = email.strip().lower().split("@", 1)[0]
    safe = _USERNAME_UNSAFE.sub("-", local).strip("-")
    if not safe:
        raise ValueError(f"이메일에서 쓸 수 있는 Username을 만들 수 없다: {email!r}")
    return safe

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
        except BotoCoreError as exc:
            # ClientError는 Cognito가 응답한 거부(코드가 있다). 이건 그 이전
            # 단계의 실패 — 네트워크 단절, 자격증명 누락, 파라미터 형식 오류 등
            # — 로 코드가 없으므로 예외 클래스 이름을 코드로 쓴다. 라우트가
            # "Cognito가 거부했다"와 "요청이 Cognito에 도달하지도 못했다"를
            # 구분할 수 있어야 한다.
            raise CognitoError(type(exc).__name__, str(exc)) from exc

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
                # 한 사용자의 그룹 조회가 실패해도(스냅샷 이후 삭제, 429 등)
                # 목록 전체를 무너뜨리지 않는다 — 그 행만 role=None으로 낮춰
                # 계속한다. role=None이 이미 "반쯤 만들어진 계정"을 표시하는
                # 값이므로 같은 표현을 재사용한다.
                try:
                    role = self._role_of(self.groups_of(username))
                except CognitoError as exc:
                    _log.debug("groups_of failed for %s: %s", username, exc.code)
                    role = None
                users.append(ManagedUser(
                    username=username,
                    email=self._email_of(raw),
                    role=role,
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

        Username은 이메일이 아니라 로컬파트다 — username_for_email 참조.
        사용자는 어느 쪽이든 이메일로 로그인한다(email alias).
        """
        username = username_for_email(email)
        # email 속성도 trim한다 — 앞뒤 공백이 남으면 alias 사인인이 그 공백까지
        # 요구하게 되어 사용자가 로그인할 수 없다.
        resp = self._call(
            "admin_create_user",
            Username=username,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email.strip()},
                            {"Name": "email_verified", "Value": "true"}],
        )
        # 응답의 Username을 신뢰한다 — 풀 설정이 바뀌어 Cognito가 다른 값을
        # 배정하면 이후 set_temp_password/set_group이 그 값으로 가야 한다.
        return resp.get("User", {}).get("Username", username)

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
