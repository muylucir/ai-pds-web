# backend/aipds/auth/cognito.py
#
# A wrapper over the Cognito Admin* APIs. The reason boto3 calls are separated from the routes
# is the same as in s3store.py: the routes deal only with policy (protecting the last
# administrator, rolling back a partial failure), and this file is verified independently with
# Stubber.
#
# This pool uses AliasAttributes(email), so the caller decides the Username -- we use the
# email's local part as the Username (see username_for_email). That makes every Admin* call
# deterministic from the email.
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

from aipds.auth.models import ROLE_ADMIN, ROLE_PM, Role

_log = logging.getLogger(__name__)

# How many users are fetched at a time. Cognito's limit is 60.
_PAGE = 60

# The role groups we manage. Groups outside this set are left alone.
_ROLE_GROUPS = (ROLE_ADMIN, ROLE_PM)

# Characters a Cognito Username will not accept are replaced with '-'. The permitted character
# set is limited (the '+' of a tagged address, for instance), so passing the local part through
# unchanged raises InvalidParameterException.
_USERNAME_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def username_for_email(email: str) -> str:
    """An email -> the Cognito Username (its local part).

    This pool uses AliasAttributes=[email]. Under that setting Cognito **rejects an
    email-format Username** ("Username cannot be of email format, since user pool is
    configured for email alias") -- because email is reserved as an alias and would collide
    with the username. Measured: not knowing this rule made seed account creation roll the
    stack back.

    So only the part before the '@' is used as the Username. The user logs in with their email
    either way (the email alias does that job).

    ⚠️ Two accounts with the same local part and different domains (kim@a.com / kim@b.com)
    collide on the same Username -- the second invitation fails with
    UsernameExistsException. A trade-off accepted at workshop scale (a single domain). To
    accept multiple domains the rule would have to include the domain, and then the identical
    rule in infra/lib/seed-users.ts has to change with it (if the two diverge, seeding becomes
    non-deterministic).
    """
    local = email.strip().lower().split("@", 1)[0]
    safe = _USERNAME_UNSAFE.sub("-", local).strip("-")
    if not safe:
        raise ValueError(f"이메일에서 쓸 수 있는 Username을 만들 수 없다: {email!r}")
    return safe

# The character classes for a temporary password. At least one character from each class is
# guaranteed so the pool policy is satisfied (8+ characters, upper, lower, digit, symbol).
# Easily confused characters (0/O, 1/l/I) are excluded -- a temporary password is a value a
# person copies through a messenger.
_LOWER = "abcdefghijkmnopqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_SYMBOLS = "!@#$%^&*_-+=?"


class CognitoError(Exception):
    """Cognito refused. `code` is the original error code (the route translates it into a\n    status code)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class ManagedUser:
    username: str      # the value used in Admin* API calls
    email: str         # the value shown on screen
    role: Role | None  # None when no group is assigned (a half-created account)
    status: str        # CONFIRMED / FORCE_CHANGE_PASSWORD / ...
    enabled: bool
    created_at: str    # ISO8601


def generate_temp_password(length: int = 16) -> str:
    """A temporary password that satisfies the policy.

    One character is picked from each class first, then the rest is filled in and shuffled.
    Drawing a random string and hoping it passes the policy fails occasionally, and that
    failure shows up as an InvalidPasswordException after the user has already been
    created.
    """
    pools = (_LOWER, _UPPER, _DIGITS, _SYMBOLS)
    chars = [secrets.choice(p) for p in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(pools))]
    # Fisher-Yates over secrets -- random.shuffle is not cryptographically secure.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


class CognitoAdmin:
    def __init__(self, client, user_pool_id: str) -> None:
        self._c = client
        self._pool = user_pool_id

    # ---- Internal ----

    def _call(self, name: str, **params):
        try:
            return getattr(self._c, name)(UserPoolId=self._pool, **params)
        except ClientError as exc:
            err = exc.response.get("Error", {})
            code = err.get("Code", "Unknown")
            raise CognitoError(code, err.get("Message", str(exc))) from exc
        except BotoCoreError as exc:
            # A ClientError is a refusal Cognito responded with (it has a code). This is a
            # failure at an earlier stage -- a dropped network, missing credentials, a
            # malformed parameter -- and has no code, so the exception class name is used as
            # the code. The route has to be able to tell "Cognito refused" from "the request
            # never even reached Cognito".
            raise CognitoError(type(exc).__name__, str(exc)) from exc

    @staticmethod
    def _email_of(raw: dict) -> str:
        for attr in raw.get("Attributes", []):
            if attr.get("Name") == "email":
                return attr.get("Value", "")
        # An account with no email attribute (created by hand, say) still has to be
        # identifiable on screen.
        return raw.get("Username", "")

    @staticmethod
    def _role_of(groups: list[str]) -> Role | None:
        # The same precedence as verifier: membership in both groups reads as admin.
        if ROLE_ADMIN in groups:
            return ROLE_ADMIN
        if ROLE_PM in groups:
            return ROLE_PM
        return None

    # ---- Reads ----

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
                # One user's group lookup failing (deleted after the snapshot, a 429) does
                # not bring down the whole listing -- that row alone is demoted to role=None
                # and it continues. role=None is already the value marking "a half-created
                # account", so the same representation is reused.
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
        """The number of members in the admin group -- the input to protecting the last\n    administrator."""
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

    # ---- Writes ----

    def create_user(self, email: str) -> str:
        """Create the user and return the Username.

        MessageAction=SUPPRESS: this app sends no mail (an invitation is the admin page showing
        the temporary password on screen once).
        email_verified=true: not a choice but a precondition of alias (email) sign-in.

        The Username is the local part rather than the email -- see username_for_email. The
        user logs in with their email either way (the email alias).
        """
        username = username_for_email(email)
        # The email attribute is trimmed too -- surrounding whitespace left in place would
        # make alias sign-in demand that whitespace as well, and the user could not log in.
        resp = self._call(
            "admin_create_user",
            Username=username,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email.strip()},
                            {"Name": "email_verified", "Value": "true"}],
        )
        # The Username from the response is trusted -- if the pool configuration changes and
        # Cognito assigns a different value, the subsequent set_temp_password and set_group
        # have to go to that value.
        return resp.get("User", {}).get("Username", username)

    def set_temp_password(self, username: str, password: str) -> None:
        """The temporary password. With Permanent=False the user changes it themselves at first\n        login."""
        self._call("admin_set_user_password", Username=username,
                   Password=password, Permanent=False)

    def set_group(self, username: str, role: Role) -> None:
        """Replace the role -- a replacement, not an addition.

        Without removing the existing role group the user would belong to admin and pm at once
        and the demotion would be void (verifier gives admin precedence). Groups outside
        admin/pm are not our concern and are left alone.
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
