from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


ROLES = {"viewer": 1, "operator": 2, "admin": 3}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str


class AuthService:
    def __init__(
        self,
        database_url: str | None,
        secret: str,
        issuer: str,
        audience: str,
        expire_minutes: int = 30,
    ) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._expire_minutes = expire_minutes
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash("dummy-password-for-timing")
        self._users: dict[str, dict] = {}
        self._engine = (
            create_engine(database_url, pool_pre_ping=True) if database_url else None
        )
        if self._engine:
            self._ensure_schema()

    def create_user(self, username: str, password: str, role: str) -> Principal:
        username = self._normalize_username(username)
        if role not in ROLES:
            raise ValueError("role must be viewer, operator, or admin")
        if not 12 <= len(password) <= 128:
            raise ValueError("password length must be between 12 and 128")

        user_id = str(uuid4())
        record = {
            "user_id": user_id,
            "username": username,
            "password_hash": self._password_hash.hash(password),
            "role": role,
            "is_active": True,
        }
        if not self._engine:
            if username in self._users:
                raise ValueError("username already exists")
            self._users[username] = record
        else:
            try:
                with self._engine.begin() as connection:
                    connection.execute(
                        text(
                            """
                            INSERT INTO users (
                                user_id, username, password_hash, role, is_active
                            ) VALUES (
                                :user_id, :username, :password_hash, :role, TRUE
                            )
                            """
                        ),
                        record,
                    )
            except IntegrityError as exc:
                raise ValueError("username already exists") from exc
        return Principal(user_id=user_id, role=role)

    def authenticate(self, username: str, password: str) -> Principal | None:
        try:
            username = self._normalize_username(username)
        except ValueError:
            username = ""
        record = self._find_by_username(username)
        password_hash = record["password_hash"] if record else self._dummy_hash
        candidate = password if len(password) <= 128 else "invalid-password"
        valid = self._password_hash.verify(candidate, password_hash)
        if not record or not valid or not record["is_active"]:
            return None
        return Principal(user_id=str(record["user_id"]), role=record["role"])

    def create_access_token(
        self, principal: Principal, expires_delta: timedelta | None = None
    ) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": principal.user_id,
                "iss": self._issuer,
                "aud": self._audience,
                "iat": now,
                "exp": now
                + (
                    expires_delta
                    if expires_delta is not None
                    else timedelta(minutes=self._expire_minutes)
                ),
            },
            self._secret,
            algorithm="HS256",
        )

    def get_principal(self, token: str) -> Principal:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["sub", "exp", "iss", "aud"]},
            )
            user_id = str(UUID(payload["sub"]))
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid token") from exc

        record = self._find_by_id(user_id)
        if not record or not record["is_active"]:
            raise ValueError("invalid token")
        return Principal(user_id=user_id, role=record["role"])

    def close(self) -> None:
        if self._engine:
            self._engine.dispose()

    def _ensure_schema(self) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id UUID PRIMARY KEY,
                        username VARCHAR(64) NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        role VARCHAR(16) NOT NULL
                            CHECK (role IN ('viewer', 'operator', 'admin')),
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CHECK (username = LOWER(username))
                    )
                    """
                )
            )

    def _find_by_username(self, username: str) -> dict | None:
        if not self._engine:
            return self._users.get(username)
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT user_id, username, password_hash, role, is_active
                    FROM users WHERE username = :username
                    """
                ),
                {"username": username},
            ).mappings().first()
        return dict(row) if row else None

    def _find_by_id(self, user_id: str) -> dict | None:
        if not self._engine:
            return next(
                (
                    record
                    for record in self._users.values()
                    if record["user_id"] == user_id
                ),
                None,
            )
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT user_id, username, password_hash, role, is_active
                    FROM users WHERE user_id = :user_id
                    """
                ),
                {"user_id": user_id},
            ).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _normalize_username(username: str) -> str:
        username = username.strip().lower()
        if not 3 <= len(username) <= 64 or any(
            character.isspace() for character in username
        ):
            raise ValueError("username must be 3-64 characters without whitespace")
        return username
