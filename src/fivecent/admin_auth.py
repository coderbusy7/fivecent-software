"""Operator authentication for the server-side Streamlit admin."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from getpass import getpass
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

PASSWORD_ITERATIONS = 600_000
SESSION_IDLE_SECONDS = 30 * 60
SESSION_MAX_SECONDS = 8 * 60 * 60
LOGIN_WINDOW_SECONDS = 60
MAX_LOGIN_FAILURES = 5


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Use an admin password with at least 12 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _parse_hash(encoded: str) -> tuple[bytes, bytes]:
    algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
    if algorithm != "pbkdf2_sha256" or iterations != str(PASSWORD_ITERATIONS):
        raise ValueError("Unsupported admin password hash.")
    salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    if len(salt) != 16 or len(digest) != 32:
        raise ValueError("Invalid admin password hash.")
    return salt, digest


def verify_password(password: str, encoded: str) -> bool:
    salt, expected = _parse_hash(encoded)
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return hmac.compare_digest(actual, expected)


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="ADMIN_", extra="ignore"
    )

    username: str = ""
    password_hash: str = ""
    api_token: str = ""
    api_base: str = "http://127.0.0.1:8000"

    def validate_configuration(self) -> None:
        if not self.username or not self.password_hash or not self.api_token:
            raise ValueError(
                "Set ADMIN_USERNAME, ADMIN_PASSWORD_HASH and ADMIN_API_TOKEN "
                "on the admin server before signing in."
            )
        _parse_hash(self.password_hash)
        url = urlsplit(self.api_base)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("ADMIN_API_BASE must be an HTTP(S) API URL without credentials.")
        if url.port is not None and url.port == 0:
            raise ValueError("ADMIN_API_BASE must use a valid TCP port.")

    @property
    def fingerprint(self) -> str:
        values = (self.username, self.password_hash, self.api_token, self.api_base)
        return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


class LoginLimiter:
    """Process-wide failure limit, shared by all Streamlit browser sessions."""

    def __init__(self) -> None:
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    def authenticate(
        self, username: str, password: str, settings: AdminSettings
    ) -> tuple[bool, bool]:
        with self._lock:
            now = time.monotonic()
            while self._failures and now - self._failures[0] >= LOGIN_WINDOW_SECONDS:
                self._failures.popleft()
            if len(self._failures) >= MAX_LOGIN_FAILURES:
                return False, True
            password_matches = verify_password(password, settings.password_hash)
            username_matches = hmac.compare_digest(
                username.encode("utf-8"), settings.username.encode("utf-8")
            )
            if username_matches and password_matches:
                return True, False
            self._failures.append(now)
            return False, False


def session_valid(
    session: object, settings: AdminSettings, *, now: float | None = None
) -> bool:
    if not isinstance(session, dict):
        return False
    current = time.monotonic() if now is None else now
    started = session.get("started")
    last_seen = session.get("last_seen")
    return (
        session.get("fingerprint") == settings.fingerprint
        and isinstance(started, (int, float))
        and isinstance(last_seen, (int, float))
        and 0 <= current - started < SESSION_MAX_SECONDS
        and 0 <= current - last_seen < SESSION_IDLE_SECONDS
    )


def main() -> None:
    password = getpass("New admin password (at least 12 characters): ")
    confirmation = getpass("Confirm admin password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    try:
        encoded = hash_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"ADMIN_PASSWORD_HASH='{encoded}'")


if __name__ == "__main__":
    main()
