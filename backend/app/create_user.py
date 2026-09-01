import argparse
from getpass import getpass

from .config import Settings
from .services.auth_service import AuthService, ROLES


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a local RAG API user")
    parser.add_argument("username")
    parser.add_argument("--role", choices=ROLES, default="viewer")
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    password = getpass("Password (12-128 characters): ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")

    service = AuthService(
        settings.database_url,
        settings.jwt_secret,
        settings.jwt_issuer,
        settings.jwt_audience,
        settings.jwt_expire_minutes,
    )
    try:
        principal = service.create_user(args.username, password, args.role)
        print(f"created {args.role} user {args.username} ({principal.user_id})")
    finally:
        service.close()


if __name__ == "__main__":
    main()
