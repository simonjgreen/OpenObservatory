"""Tests for the Milestone 4 authentication foundation (ADR-034, closes ADR-015).

Two layers are exercised:

* `open_observatory.auth` directly (`TestAuthServiceUnit`) -- password hashing
  never returning plaintext, tokens/sessions round-tripping, the rate
  limiter's own arithmetic -- against a real SQLite database via
  `db.session`, exactly like `test_retention.py`'s pattern, with no HTTP
  layer involved.
* The real FastAPI app through `TestClient` (`TestAuthWiredThroughApi`) --
  the blanket gate, login/logout, session and token auth, the rate limit as
  seen through the endpoint, and -- explicitly, because a regression here
  goes dark on someone's counter top -- the two paths the ESP32 inside-observer
  display polls unauthenticated (`GET /api/v1/health`,
  `GET /api/v1/detections`) staying reachable with `auth_enabled=True`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from open_observatory import auth as auth_module
from open_observatory.api.app import create_app
from open_observatory.auth import AuthError, AuthService, RateLimiter, hash_password
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

# ---------------------------------------------------------------------------
# Direct unit tests against AuthService / the DB, no HTTP.
# ---------------------------------------------------------------------------


@pytest.fixture
def db(settings):
    init_engine(settings)
    create_all()
    return settings


class TestPasswordHashing:
    def test_hash_never_contains_the_plaintext(self, settings) -> None:
        secret = "correct horse battery staple 42"
        hashed = hash_password(settings, secret)
        assert secret not in hashed
        assert hashed.startswith("$argon2id$")

    def test_verify_accepts_the_right_password_and_rejects_others(self, settings) -> None:
        hashed = hash_password(settings, "s3cret-passphrase!!")
        assert auth_module.verify_password(settings, "s3cret-passphrase!!", hashed) is True
        assert auth_module.verify_password(settings, "wrong-passphrase", hashed) is False

    def test_password_policy_rejects_short_passwords(self, settings) -> None:
        with pytest.raises(AuthError):
            auth_module.validate_password_policy(settings, "short")
        # does not raise:
        auth_module.validate_password_policy(settings, "x" * settings.auth_password_min_length)


class TestBootstrap:
    def test_bootstrap_generates_and_hashes_a_password_once(self, db) -> None:
        service = AuthService(db)
        with session_scope() as session:
            password = service.bootstrap_admin_if_needed(session)
        assert password is not None
        assert len(password) >= 16

        with session_scope() as session:
            user = session.execute(select(orm.User)).scalars().one()
            assert user.username == db.auth_bootstrap_username
            assert user.must_change_password is True
            # The generated password is never the thing stored.
            assert password not in user.password_hash
            assert user.password_hash.startswith("$argon2id$")

        # Second call against a non-empty user table is a no-op.
        with session_scope() as session:
            assert service.bootstrap_admin_if_needed(session) is None

    def test_login_failure_never_logs_the_plaintext_password(self, db, monkeypatch) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        class _CapturingLog:
            def warning(self, event: str, **kw: object) -> None:
                calls.append((event, kw))

            def info(self, event: str, **kw: object) -> None:
                calls.append((event, kw))

            def error(self, event: str, **kw: object) -> None:
                calls.append((event, kw))

            def exception(self, event: str, **kw: object) -> None:
                calls.append((event, kw))

        monkeypatch.setattr(auth_module, "log", _CapturingLog())
        service = AuthService(db)
        with session_scope() as session:
            service.bootstrap_admin_if_needed(session)

        secret = "th1s-must-never-appear-in-a-log-line"
        with session_scope() as session, pytest.raises(AuthError):
            service.authenticate(session, username=db.auth_bootstrap_username, password=secret)

        assert calls, "expected authenticate() failure path to log something"
        for _event, kw in calls:
            assert secret not in repr(kw)


class TestSessionsAndTokens:
    def test_session_round_trips_and_expires(self, db) -> None:
        service = AuthService(db)
        with session_scope() as session:
            service.bootstrap_admin_if_needed(session)
            user = session.execute(select(orm.User)).scalars().one()
            token, row = service.create_session(session, user=user)

        with session_scope() as session:
            principal = service.resolve_session(session, token)
            assert principal is not None
            assert principal.username == db.auth_bootstrap_username
            assert principal.method == "session"

        # A forged/unknown token resolves to nothing.
        with session_scope() as session:
            assert service.resolve_session(session, "not-a-real-token") is None

        # An expired session is rejected even though the token is genuine.
        with session_scope() as session:
            live_row = session.get(orm.AuthSession, row.id)
            assert live_row is not None
            live_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        with session_scope() as session:
            assert service.resolve_session(session, token) is None

    def test_revoked_session_is_rejected(self, db) -> None:
        service = AuthService(db)
        with session_scope() as session:
            service.bootstrap_admin_if_needed(session)
            user = session.execute(select(orm.User)).scalars().one()
            token, _row = service.create_session(session, user=user)
        with session_scope() as session:
            service.revoke_session(session, token)
        with session_scope() as session:
            assert service.resolve_session(session, token) is None

    def test_api_token_round_trips_and_can_be_revoked(self, db) -> None:
        service = AuthService(db)
        with session_scope() as session:
            service.bootstrap_admin_if_needed(session)
            user = session.execute(select(orm.User)).scalars().one()
            token, row = service.create_api_token(session, user=user, name="esp32-followup")

        assert token.startswith(auth_module.API_TOKEN_PREFIX)

        with session_scope() as session:
            principal = service.resolve_api_token(session, token)
            assert principal is not None
            assert principal.method == "token"

        with session_scope() as session:
            user = session.execute(select(orm.User)).scalars().one()
            assert service.revoke_api_token(session, user=user, token_id=row.id) is True

        with session_scope() as session:
            assert service.resolve_api_token(session, token) is None


class TestRateLimiter:
    def test_allows_up_to_the_limit_then_blocks(self) -> None:
        limiter = RateLimiter(max_attempts=3, window_s=60.0)
        results = [limiter.allow("1.2.3.4")[0] for _ in range(4)]
        assert results == [True, True, True, False]

    def test_reset_clears_the_key(self) -> None:
        limiter = RateLimiter(max_attempts=1, window_s=60.0)
        assert limiter.allow("k")[0] is True
        assert limiter.allow("k")[0] is False
        limiter.reset("k")
        assert limiter.allow("k")[0] is True

    def test_distinct_keys_do_not_share_a_budget(self) -> None:
        limiter = RateLimiter(max_attempts=1, window_s=60.0)
        assert limiter.allow("a")[0] is True
        assert limiter.allow("b")[0] is True


# ---------------------------------------------------------------------------
# Through the real app.
# ---------------------------------------------------------------------------


def _bootstrap_password(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr().out
    match = re.search(r"Generated password \(shown once\): (\S+)", out)
    assert match, f"bootstrap password was not printed to stdout: {out!r}"
    return match.group(1)


@pytest.fixture
def disabled_client(settings):
    configured = settings.model_copy(update={"auth_enabled": False})
    set_settings(configured)
    app = create_app(configured)
    with TestClient(app) as client:
        yield client, configured


@pytest.fixture
def enabled_app(settings, capsys):
    configured = settings.model_copy(update={"auth_enabled": True})
    set_settings(configured)
    app = create_app(configured)
    with TestClient(app) as client:
        password = _bootstrap_password(capsys)
        yield client, configured, password


class TestAuthDisabledIsUnchangedBehaviour:
    def test_anonymous_read_and_write_both_work(self, disabled_client) -> None:
        client, _ = disabled_client
        assert client.get("/api/v1/detections").status_code == 200
        assert client.get("/api/v1/streams").status_code == 200

    def test_me_reports_auth_disabled_without_requiring_login(self, disabled_client) -> None:
        client, _ = disabled_client
        body = client.get("/api/v1/auth/me").json()
        assert body == {"authenticated": False, "auth_enabled": False}

    def test_health_does_not_flag_auth_as_a_problem_by_default(self, disabled_client) -> None:
        client, _ = disabled_client
        body = client.get("/api/v1/health").json()
        assert body["auth"] == {"enabled": False}
        assert not any("auth" in problem.lower() for problem in body["problems"])


class TestAuthEnabledBlanketGate:
    def test_unauthenticated_protected_endpoint_is_refused(self, enabled_app) -> None:
        client, _configured, _password = enabled_app
        response = client.get("/api/v1/streams")
        assert response.status_code == 401

    def test_unauthenticated_write_endpoint_is_refused(self, enabled_app) -> None:
        client, _configured, _password = enabled_app
        response = client.post(
            "/api/v1/detections/00000000-0000-0000-0000-000000000000/review",
            json={"status": "confirmed"},
        )
        assert response.status_code == 401

    def test_esp32_counter_top_display_endpoints_stay_public(self, enabled_app) -> None:
        """Regression guard: the ESP32 inside-observer display cannot be
        reflashed as part of this change (see ADR-034) and polls these two
        paths every `pollSeconds` with no credential at all. If this test
        ever fails, the display goes dark."""
        client, _configured, _password = enabled_app
        # 503 is a legitimate answer here (capture has not delivered its
        # first block yet -- a startup race, not an auth failure); 401 is
        # not, and that is the only thing this test cares about.
        assert client.get("/api/v1/health").status_code in (200, 503)
        assert client.get("/api/v1/detections").status_code == 200
        assert client.get("/metrics").status_code == 200
        # The allow-list is GET-only and path-specific: neither a write to
        # the same path nor an unrelated path is accidentally exempted.
        assert client.post("/api/v1/detections").status_code == 401
        assert client.get("/api/v1/detections/export").status_code == 401

    def test_health_flags_auth_enabled_with_no_active_user_as_a_problem(self, enabled_app) -> None:
        client, _configured, _password = enabled_app
        with session_scope() as session:
            user = session.execute(select(orm.User)).scalars().one()
            user.disabled_at = datetime.now(UTC)
            session.commit()
        body = client.get("/api/v1/health").json()
        assert body["auth"]["enabled"] is True
        assert body["auth"]["active_users"] == 0
        assert any("locked out" in problem for problem in body["problems"])


class TestLoginSessionAndTokenFlow:
    def test_valid_login_creates_a_session_that_authenticates_requests(self, enabled_app) -> None:
        client, configured, password = enabled_app
        response = client.post(
            "/api/v1/auth/login",
            json={"username": configured.auth_bootstrap_username, "password": password},
        )
        assert response.status_code == 200
        assert response.json()["must_change_password"] is True
        assert configured.auth_session_cookie_name in client.cookies

        assert client.get("/api/v1/streams").status_code == 200
        me = client.get("/api/v1/auth/me").json()
        assert me["authenticated"] is True
        assert me["username"] == configured.auth_bootstrap_username
        assert me["method"] == "session"

    def test_wrong_password_is_refused_and_does_not_authenticate(self, enabled_app) -> None:
        client, configured, _password = enabled_app
        response = client.post(
            "/api/v1/auth/login",
            json={"username": configured.auth_bootstrap_username, "password": "definitely-wrong"},
        )
        assert response.status_code == 401
        assert configured.auth_session_cookie_name not in client.cookies

    def test_logout_revokes_the_session(self, enabled_app) -> None:
        client, configured, password = enabled_app
        client.post(
            "/api/v1/auth/login",
            json={"username": configured.auth_bootstrap_username, "password": password},
        )
        assert client.get("/api/v1/streams").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get("/api/v1/streams").status_code == 401

    def test_forged_session_cookie_is_rejected(self, enabled_app) -> None:
        client, configured, _password = enabled_app
        client.cookies.set(configured.auth_session_cookie_name, "forged-token-value")
        assert client.get("/api/v1/streams").status_code == 401

    def test_expired_session_is_rejected(self, enabled_app) -> None:
        client, configured, password = enabled_app
        client.post(
            "/api/v1/auth/login",
            json={"username": configured.auth_bootstrap_username, "password": password},
        )
        assert client.get("/api/v1/streams").status_code == 200
        with session_scope() as session:
            row = session.execute(select(orm.AuthSession)).scalars().one()
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        assert client.get("/api/v1/streams").status_code == 401

    def test_api_token_authenticates_a_credential_free_machine_client(self, enabled_app) -> None:
        client, configured, password = enabled_app
        client.post(
            "/api/v1/auth/login",
            json={"username": configured.auth_bootstrap_username, "password": password},
        )
        created = client.post("/api/v1/auth/tokens", json={"name": "esp32-followup"})
        assert created.status_code == 200
        token = created.json()["token"]
        token_id = created.json()["id"]

        # A separate client, sharing the same running app but with no cookie
        # jar at all -- exactly the shape of a script or a future firmware
        # request, per ADR-034.
        machine = TestClient(client.app)
        assert machine.get("/api/v1/streams").status_code == 401
        authed = machine.get("/api/v1/streams", headers={"Authorization": f"Bearer {token}"})
        assert authed.status_code == 200

        revoke = client.delete(f"/api/v1/auth/tokens/{token_id}")
        assert revoke.status_code == 200
        after_revoke = machine.get("/api/v1/streams", headers={"Authorization": f"Bearer {token}"})
        assert after_revoke.status_code == 401

    def test_change_password_requires_the_current_one(self, enabled_app) -> None:
        client, configured, password = enabled_app
        client.post(
            "/api/v1/auth/login",
            json={"username": configured.auth_bootstrap_username, "password": password},
        )
        wrong = client.post(
            "/api/v1/auth/password",
            json={"current_password": "not-it", "new_password": "a-new-long-enough-password"},
        )
        assert wrong.status_code == 400

        right = client.post(
            "/api/v1/auth/password",
            json={"current_password": password, "new_password": "a-new-long-enough-password"},
        )
        assert right.status_code == 200
        me = client.get("/api/v1/auth/me").json()
        assert me["must_change_password"] is False


class TestAuthPublicReadPathsFromEnv:
    """`auth_public_read_paths` is a tuple field, and pydantic-settings tries
    to JSON-decode any tuple field's raw env value before this project's own
    comma-splitting validator runs -- which breaks on a plain comma-separated
    value. This field is annotated `NoDecode` specifically so it does not
    inherit that (pre-existing, and not fixed here) breakage; this test
    guards the annotation, not just the validator."""

    def test_comma_separated_env_value_parses(self, monkeypatch) -> None:
        monkeypatch.setenv("OO_AUTH_PUBLIC_READ_PATHS", "/api/v1/detections,/api/v1/foo")
        from open_observatory.config import Settings

        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.auth_public_read_paths == ("/api/v1/detections", "/api/v1/foo")


class TestLoginRateLimiting:
    def test_login_endpoint_rate_limits_repeated_failures(self, enabled_app) -> None:
        client, configured, _password = enabled_app
        for _ in range(configured.auth_login_rate_limit_attempts):
            response = client.post(
                "/api/v1/auth/login", json={"username": "nobody", "password": "wrong"}
            )
            assert response.status_code == 401

        limited = client.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": "wrong"}
        )
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers
