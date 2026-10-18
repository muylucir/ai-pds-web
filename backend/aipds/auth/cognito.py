# backend/aipds/auth/cognito.py
#
# Wrapper for the Cognito Admin* APIs. The reason boto3 calls are separated from the routes
# is the same as for s3store.py: routes deal only with policy (last-admin protection, partial
# failure rollback), and this file is verified independently with Stubber.
#
# This pool uses AliasAttributes(email), so the caller decides the Username — we use the
# local part of the email as the Username (see username_for_email). So every Admin* call
# is deterministic from the email.
from __future__ import annotations

import logging
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

from aipds.auth.models import ROLE_ADMIN, ROLE_PM, Role

_log = logging.getLogger(__name__)

# Number of users fetched at once. Cognito's limit is 60.
_PAGE = 60

# The role groups we manage. Groups outside this set are not touched.
_ROLE_GROUPS = (ROLE_ADMIN, ROLE_PM)

# Replace characters that Cognito Username does not accept with '-'. The characters allowed
# in a Username are limited (e.g., the '+' in tagged addresses), so passing the local part
# as is raises InvalidParameterException.
_USERNAME_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def username_for_email(email: str) -> str:
    """Email → Cognito Username (local part).

    This pool is AliasAttributes=[email]. With that setting, Cognito **rejects
    email-format Usernames** ("Username cannot be of email format, since user pool
    is configured for email alias") — because email is reserved as an alias and would
    collide with the username. Measured: not knowing this rule, seed account creation caused a stack rollback.

    So only the part before '@' is used as the Username. Either way, users sign in with
    their email (the email alias does that).

    ⚠️ Two accounts with the same local part and different domains (kim@example.com / kim@example.org)
    collide on the same Username — the second invite fails with UsernameExistsException.
    This is a trade-off accepted at workshop scale (single domain). If multiple domains must
    be supported, the rule must change to include the domain, and then the same rule in
    infra/lib/seed-users.ts must be fixed too (if the two diverge, seeding becomes nondeterministic).
    """
    local = email.strip().lower().split("@", 1)[0]
    safe = _USERNAME_UNSAFE.sub("-", local).strip("-")
    if not safe:
        raise ValueError(f"이메일에서 쓸 수 있는 Username을 만들 수 없다: {email!r}")
    return safe

# Character classes for temporary passwords. At least 1 character from each class is guaranteed
# to satisfy the pool policy (8+ chars, upper/lower/digit/symbol). Easily confused characters
# (0/O, 1/l/I) are excluded — a temporary password is something a person copies over a messenger.
_LOWER = "abcdefghijkmnopqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_SYMBOLS = "!@#$%^&*_-+=?"


class CognitoError(Exception):
    """Cognito rejected the call. `code` is the raw error code (the route translates it into a status code).

    `message` is kept too because one code really can mean two different situations:
    `ChangePassword`'s `NotAuthorizedException` can mean "the current password is
    wrong" or "the token lacks the self-service scope", with the same code and only the
    message differing. What the user must do in the two cases is opposite
    (routes/account.py), so the route must be able to tell them apart. The message is not
    shown to the user — it is used only for branching and logs.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@contextmanager
def _as_cognito_error():
    """Convert a boto3 exception into CognitoError.

    `CognitoAdmin._call` and the self-service call (`change_own_password`) must use the
    same translation — with two copies, one side misses BotoCoreError and the route cannot
    tell "Cognito rejected it" from "the request never even reached Cognito".
    """
    try:
        yield
    except ClientError as exc:
        err = exc.response.get("Error", {})
        raise CognitoError(
            err.get("Code", "Unknown"),
            err.get("Message", str(exc))) from exc
    except BotoCoreError as exc:
        # ClientError is a rejection Cognito responded with (it has a code). This is a failure
        # at an earlier stage — network disconnect, missing credentials, malformed parameters,
        # etc. — and has no code, so the exception class name is used as the code.
        raise CognitoError(type(exc).__name__, str(exc)) from exc


def change_own_password(client, access_token: str, previous: str,
                        proposed: str) -> None:
    """Change one's own password. **Not an Admin\\* call.**

    `ChangePassword` takes neither a UserPoolId nor a Username — the access token is the
    identity, so there is structurally no way to change someone else's password through this
    channel. That is why it is not a method of `CognitoAdmin` (that class's `_call` attaches the
    UserPoolId to every call). The backend needs no additional IAM permission either.

    The cost: the token must carry the `aws.cognito.signin.user.admin` scope
    (`COGNITO_ADMIN_SCOPE` in infra/lib/auth-client-config.ts). Without it, Cognito
    rejects the call with `NotAuthorizedException`.

    Cognito performs the current-password verification and the policy check — we do not repeat
    them here. Two copies of a policy inevitably diverge, and the diverging side lies to the user.
    """
    with _as_cognito_error():
        client.change_password(
            AccessToken=access_token,
            PreviousPassword=previous,
            ProposedPassword=proposed,
        )


@dataclass
class ManagedUser:
    username: str      # value used for Admin* API calls
    email: str         # value shown on screen
    role: Role | None  # None if no group is assigned (a half-created account)
    status: str        # CONFIRMED / FORCE_CHANGE_PASSWORD / ...
    enabled: bool
    created_at: str    # ISO8601


def generate_temp_password(length: int = 16) -> str:
    """A temporary password that satisfies the policy.

    Picks 1 character from each class first, fills in the rest, and shuffles. Drawing a random
    string and hoping it passes the policy fails occasionally, and that failure shows up as
    InvalidPasswordException after the user has already been created.
    """
    pools = (_LOWER, _UPPER, _DIGITS, _SYMBOLS)
    chars = [secrets.choice(p) for p in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(pools))]
    # Fisher-Yates based on secrets — random.shuffle is not cryptographically secure.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def _filter_value(value: str) -> str:
    """Value to place inside the quotes of a ListUsers Filter. Escapes quotes and backslashes — callers
    pass only values that passed the username rule (project_access.username_of), but we do not let a value
    that breaks the filter syntax select a different user."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class CognitoAdmin:
    def __init__(self, client, user_pool_id: str) -> None:
        self._c = client
        self._pool = user_pool_id

    # ---- Internal ----

    def _call(self, name: str, **params):
        # Exception translation happens in one place, _as_cognito_error — it must follow
        # the same rules as the self-service call (see that function's comment).
        with _as_cognito_error():
            return getattr(self._c, name)(UserPoolId=self._pool, **params)

    @staticmethod
    def _email_of(raw: dict) -> str:
        for attr in raw.get("Attributes", []):
            if attr.get("Name") == "email":
                return attr.get("Value", "")
        # Accounts without an email attribute (created manually, etc.) must still be identifiable on screen.
        return raw.get("Username", "")

    @staticmethod
    def _role_of(groups: list[str]) -> Role | None:
        # Same precedence as the verifier: if in both groups, treat as admin.
        if ROLE_ADMIN in groups:
            return ROLE_ADMIN
        if ROLE_PM in groups:
            return ROLE_PM
        return None

    # ---- Queries ----

    def groups_of(self, username: str) -> list[str]:
        resp = self._call("admin_list_groups_for_user", Username=username)
        return [g["GroupName"] for g in resp.get("Groups", [])]

    def is_enabled(self, username: str) -> bool:
        """Whether the account is enabled. A missing account raises UserNotFoundException (CognitoError).

        Reads via a ListUsers filter rather than AdminGetUser. The instance role allows only the APIs this
        module calls (infra/lib/aipds-hosting-stack.ts), and growing that list requires redeploying
        HostingStack, which replaces the instance. ListUsers is already allowed and carries
        Enabled."""
        resp = self._call("list_users", Filter=f'username = "{_filter_value(username)}"',
                          Limit=1)
        users = resp.get("Users", [])
        if not users:
            raise CognitoError("UserNotFoundException", f"{username} not found")
        return bool(users[0].get("Enabled", True))

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
                # Even if one user's group lookup fails (deleted after the snapshot, 429, etc.),
                # do not bring down the whole list — demote just that row to role=None and
                # continue. role=None is already the value that marks "a half-created account",
                # so the same representation is reused.
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
        """Number of admin group members — the input to last-admin protection."""
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

    # ---- Mutations ----

    def create_user(self, email: str) -> str:
        """Create a user and return the Username.

        MessageAction=SUPPRESS: this app does not send email (for invites, the admin page
        shows the temporary password on screen once).
        email_verified=true: not optional, but a condition for alias(email) sign-in.

        The Username is the local part, not the email — see username_for_email.
        Either way, users sign in with their email (email alias).
        """
        username = username_for_email(email)
        # Trim the email attribute too — if leading/trailing spaces remain, alias sign-in
        # would require those spaces as well, and the user could not sign in.
        resp = self._call(
            "admin_create_user",
            Username=username,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email.strip()},
                            {"Name": "email_verified", "Value": "true"}],
        )
        # Trust the Username in the response — if the pool configuration changes and Cognito
        # assigns a different value, subsequent set_temp_password/set_group must use that value.
        return resp.get("User", {}).get("Username", username)

    def set_temp_password(self, username: str, password: str) -> None:
        """Temporary password. Permanent=False, so the user changes it themselves on first sign-in."""
        self._call("admin_set_user_password", Username=username,
                   Password=password, Permanent=False)

    def set_group(self, username: str, role: Role) -> None:
        """Replace the role — replace, not add.

        If the existing role group is not removed, the user belongs to both admin and pm
        and the demotion is void (the verifier gives admin precedence). Groups outside
        admin/pm are not our concern, so they are not touched.
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
