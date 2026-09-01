import os
import unittest
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import create_engine, text

from app.services.auth_service import AuthService


class AuthServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AuthService(
            None, "test-secret-that-is-at-least-32-bytes", "test-issuer", "test-api"
        )

    def tearDown(self) -> None:
        self.service.close()

    def test_password_token_and_validation(self) -> None:
        principal = self.service.create_user(
            "Viewer.One", "Secure-pass-123!", "viewer"
        )
        authenticated = self.service.authenticate(
            "viewer.one", "Secure-pass-123!"
        )

        self.assertEqual(authenticated, principal)
        self.assertIsNone(self.service.authenticate("viewer.one", "wrong-password"))
        self.assertEqual(
            self.service.get_principal(
                self.service.create_access_token(principal)
            ),
            principal,
        )
        with self.assertRaises(ValueError):
            self.service.get_principal(
                self.service.create_access_token(
                    principal, expires_delta=timedelta(seconds=-1)
                )
            )


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 to test PostgreSQL users",
)
class AuthPostgresTest(unittest.TestCase):
    def test_password_is_hashed_and_user_persists(self) -> None:
        database_url = os.environ["DATABASE_URL"]
        username = f"m6-{uuid4().hex}"
        password = "Database-pass-123!"
        service = AuthService(
            database_url,
            "test-secret-that-is-at-least-32-bytes",
            "test-issuer",
            "test-api",
        )
        principal = service.create_user(username, password, "operator")
        token = service.create_access_token(principal)
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                stored = connection.execute(
                    text("SELECT password_hash FROM users WHERE user_id = :user_id"),
                    {"user_id": principal.user_id},
                ).scalar_one()
                connection.execute(
                    text("UPDATE users SET role = 'admin' WHERE user_id = :user_id"),
                    {"user_id": principal.user_id},
                )
            self.assertNotEqual(stored, password)
            self.assertTrue(stored.startswith("$argon2"))
            self.assertEqual(service.get_principal(token).role, "admin")

            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE users SET is_active = FALSE WHERE user_id = :user_id"),
                    {"user_id": principal.user_id},
                )
            with self.assertRaises(ValueError):
                service.get_principal(token)
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM users WHERE user_id = :user_id"),
                    {"user_id": principal.user_id},
                )
            engine.dispose()
            service.close()
