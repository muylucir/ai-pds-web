# backend/aipds/auth/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 역할은 둘뿐이고, 출처는 Cognito 그룹 멤버십(cognito:groups)이다. 커스텀 속성으로
# role을 두지 않는다 — 진실이 두 곳에 생기면 어긋난다.
Role = Literal["admin", "pm"]

ROLE_ADMIN: Role = "admin"
ROLE_PM: Role = "pm"


@dataclass(frozen=True)
class Principal:
    """검증을 통과한 요청자.

    email이 없는 것이 의도다: Cognito **access** 토큰에는 email 클레임이 존재하지
    않는다(기본 payload는 sub/username/cognito:groups/client_id/token_use/scope).
    화면에 표시할 이메일은 프론트가 id 토큰에서 읽는다.
    """
    username: str
    sub: str
    role: Role
