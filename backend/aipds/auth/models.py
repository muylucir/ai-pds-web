# backend/aipds/auth/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# There are only two roles, and their source is Cognito group membership (cognito:groups). A
# role is not kept as a custom attribute -- two homes for the truth means they diverge.
Role = Literal["admin", "pm"]

ROLE_ADMIN: Role = "admin"
ROLE_PM: Role = "pm"


@dataclass(frozen=True)
class Principal:
    """A requester that passed verification.

    The absence of email is deliberate: a Cognito **access** token has no email claim (the
    default payload is sub/username/cognito:groups/client_id/token_use/scope). The email to
    display on screen is read by the frontend from the id token.
    """
    username: str
    sub: str
    role: Role
