"""JWT (access/refresh), password hashing and FastAPI auth dependencies."""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import errors
from app.config import settings
from app.generated.models import Role

ACCESS = "access"
REFRESH = "refresh"


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def issue_token(user_id: UUID, role: Role, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == ACCESS
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    payload = {"sub": str(user_id), "role": role.value, "type": token_type, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, token_type: str) -> Principal:
    """Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError on bad tokens."""
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm], options={"require": ["sub", "exp", "type"]}
    )
    if payload.get("type") != token_type:
        raise jwt.InvalidTokenError(f"expected {token_type} token")
    try:
        return Principal(user_id=UUID(payload["sub"]), role=Role(payload["role"]))
    except (KeyError, ValueError) as exc:
        raise jwt.InvalidTokenError("malformed claims") from exc


_bearer = HTTPBearer(auto_error=False)


def authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Principal:
    if credentials is None:
        raise errors.token_invalid("Missing Authorization: Bearer <token> header")
    try:
        principal = decode_token(credentials.credentials, ACCESS)
    except jwt.ExpiredSignatureError:
        raise errors.token_expired()
    except jwt.InvalidTokenError:
        raise errors.token_invalid()
    request.state.user_id = str(principal.user_id)  # picked up by the request logger
    return principal


def require_roles(roles: Sequence[str]):
    allowed = {Role(r) for r in roles}

    def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        if principal.role not in allowed:
            raise errors.access_denied(f"Role {principal.role.value} is not allowed; required one of {sorted(roles)}")
        return principal

    return dependency
