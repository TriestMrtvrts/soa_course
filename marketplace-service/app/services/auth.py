import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.config import settings
from app.db import User
from app.generated.models import LoginRequest, RefreshRequest, RegisterRequest, Role, TokenPair
from app.security import ACCESS, REFRESH, decode_token, hash_password, issue_token, verify_password


class AuthService:
    def __init__(self, session: Session):
        self.session = session

    def register(self, body: RegisterRequest) -> User:
        email = body.email.lower()
        if self.session.scalar(select(User.id).where(User.email == email)) is not None:
            raise errors.ApiError(409, "USER_ALREADY_EXISTS", "User with this email already exists", {"email": email})
        user = User(email=email, password_hash=hash_password(body.password), role=Role(body.role.value))
        self.session.add(user)
        self.session.flush()
        return user

    def login(self, body: LoginRequest) -> TokenPair:
        user = self.session.scalar(select(User).where(User.email == body.email.lower()))
        if user is None or not verify_password(body.password, user.password_hash):
            raise errors.invalid_credentials()
        return self._token_pair(user)

    def refresh(self, body: RefreshRequest) -> TokenPair:
        try:
            principal = decode_token(body.refresh_token, REFRESH)
        except jwt.InvalidTokenError:  # includes ExpiredSignatureError
            raise errors.refresh_token_invalid()
        user = self.session.get(User, principal.user_id)
        if user is None:
            raise errors.refresh_token_invalid()
        # Role is re-read from the DB, so a role change takes effect on the next refresh.
        return self._token_pair(user)

    @staticmethod
    def _token_pair(user: User) -> TokenPair:
        return TokenPair(
            access_token=issue_token(user.id, user.role, ACCESS),
            refresh_token=issue_token(user.id, user.role, REFRESH),
            token_type="Bearer",
            expires_in=settings.access_token_ttl_minutes * 60,
        )
