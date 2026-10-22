import pytest

from fivecent.admin_auth import (
    AdminSettings,
    LoginLimiter,
    PASSWORD_ITERATIONS,
    SESSION_IDLE_SECONDS,
    SESSION_MAX_SECONDS,
    hash_password,
    session_valid,
    verify_password,
)


@pytest.fixture
def admin_settings():
    return AdminSettings(
        _env_file=None,
        username="operator",
        password_hash=hash_password("a-long-test-password"),
        api_token="backend-test-token",
    )


def test_password_hash_is_salted_and_verified(admin_settings):
    encoded = admin_settings.password_hash
    assert encoded.startswith(f"pbkdf2_sha256${PASSWORD_ITERATIONS}$")
    assert encoded != hash_password("a-long-test-password")
    assert verify_password("a-long-test-password", encoded)
    assert not verify_password("wrong-password", encoded)
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("short")


@pytest.mark.parametrize("encoded", ["", "plain-text", "pbkdf2_sha256$1$aa$bb"])
def test_invalid_hash_is_configuration_error(admin_settings, encoded):
    admin_settings.password_hash = encoded
    with pytest.raises(ValueError):
        admin_settings.validate_configuration()


@pytest.mark.parametrize("field", ["username", "password_hash", "api_token"])
def test_missing_configuration_fails_closed(admin_settings, field):
    setattr(admin_settings, field, "")
    with pytest.raises(ValueError):
        admin_settings.validate_configuration()


@pytest.mark.parametrize("url", ["file:///tmp/api", "https://user:pass@api.test", "https://api.test?token=x"])
def test_unsafe_api_urls_are_rejected(admin_settings, url):
    admin_settings.api_base = url
    with pytest.raises(ValueError, match="HTTP"):
        admin_settings.validate_configuration()


def test_login_throttle_is_shared_and_expires(admin_settings, monkeypatch):
    limiter = LoginLimiter()
    monkeypatch.setattr("fivecent.admin_auth.time.monotonic", lambda: 100.0)
    for _ in range(5):
        assert limiter.authenticate("wrong-user", "a-long-test-password", admin_settings) == (False, False)
    assert limiter.authenticate("operator", "a-long-test-password", admin_settings) == (False, True)
    monkeypatch.setattr("fivecent.admin_auth.time.monotonic", lambda: 160.0)
    assert limiter.authenticate("operator", "a-long-test-password", admin_settings) == (True, False)


def test_sessions_expire_and_configuration_changes_revoke_them(admin_settings):
    session = {
        "fingerprint": admin_settings.fingerprint,
        "started": 100.0,
        "last_seen": 100.0,
    }
    assert not session_valid(None, admin_settings, now=101)
    assert session_valid(session, admin_settings, now=101)
    assert not session_valid(session, admin_settings, now=100 + SESSION_IDLE_SECONDS)
    session["last_seen"] = 100 + SESSION_MAX_SECONDS - 1
    assert not session_valid(session, admin_settings, now=100 + SESSION_MAX_SECONDS)
    session["last_seen"] = 100.0
    admin_settings.api_token = "rotated-token"
    assert not session_valid(session, admin_settings, now=101)
