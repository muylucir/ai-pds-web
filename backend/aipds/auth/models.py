# backend/aipds/auth/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# There are only two roles, and their source is Cognito group membership (cognito:groups). role is not
# kept as a custom attribute — having the truth in two places makes them drift.
Role = Literal["admin", "pm"]

ROLE_ADMIN: Role = "admin"
ROLE_PM: Role = "pm"


@dataclass(frozen=True)
class Principal:
    """A requester that passed verification.

    The absence of email is intentional: Cognito **access** tokens have no email claim
    (the default payload is sub/username/cognito:groups/client_id/token_use/scope).
    The email shown on screen is read by the frontend from the id token.
    """
    username: str
    sub: str
    role: Role
    #: The token's `iat` (epoch seconds). Identifies tokens from sessions an admin cut off (auth/revocation.py).
    #: None for requesters that did not come from a token (local development).
    issued_at: float | None = None
