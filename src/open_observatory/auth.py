"""Authentication foundation for the local control plane (Milestone 4, ADR-034).

Closes ADR-015: the debug slice has run with anonymous read and no authentication
at all since Milestone 0. This module is deliberately small -- local accounts,
hashed passwords, browser session cookies, and revocable API tokens for machine
clients -- not an identity system. There is one operator, on one LAN appliance,
and the shape reflects that.

**What this protects against, stated honestly (see ADR-034 for the full
reasoning):** a device on the same network that would otherwise have read/write
access to the API with zero credential now needs either a valid session cookie
or a valid `Authorization: Bearer` token. Passwords are hashed with Argon2id and
never logged. Session and API tokens are stored only as SHA-256 hashes, so a
stolen database dump cannot be replayed directly.

**What this does NOT protect against:** the station is served over plain HTTP
with no TLS component anywhere in this codebase, so a session cookie or bearer
token crosses the LAN in the clear and is trivially sniffable by anything else
on that network (a compromised IoT device, a malicious guest on the WiFi, an
untrusted router). This is not a design flaw introduced here -- ADR-015 already
recorded LAN-only trust as the whole point of the debug slice -- but layering a
login page on top of plaintext HTTP must not be read as "now secure". It is one
more gate on an already-open path, not a fence around it.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import models as orm

log = structlog.get_logger(__name__)

#: Prefix on every issued API token, both so a token is recognisable at a
#: glance (in a log line, in a leaked screenshot) and so a bearer value that
#: is obviously not one of ours can be rejected before touching the database.
API_TOKEN_PREFIX = "oo_"
_TOKEN_LOOKUP_PREFIX_LEN = 16


class AuthError(ValueError):
    """A caller-facing authentication/authorisation failure. Never carries a
    plaintext secret in its message -- every raise site below is checked for
    that."""


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller of a request, however they authenticated."""

    user_id: uuid.UUID
    username: str
    method: Literal["session", "token"]
    must_change_password: bool = False


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def _hasher(settings: Settings) -> PasswordHasher:
    """Built fresh from settings each call -- cheap (no state to warm up) and
    means a config change takes effect without restructuring call sites."""
    return PasswordHasher(
        time_cost=settings.auth_argon2_time_cost,
        memory_cost=settings.auth_argon2_memory_cost_kib,
        parallelism=settings.auth_argon2_parallelism,
    )


def hash_password(settings: Settings, password: str) -> str:
    """Argon2id PHC hash. The plaintext is never returned, logged, or stored
    anywhere else in this codebase -- this function's return value is the
    only thing that touches the database."""
    return _hasher(settings).hash(password)


def verify_password(settings: Settings, password: str, password_hash: str) -> bool:
    """True if `password` matches `password_hash`. Never raises on a wrong
    password or a corrupt/foreign hash -- both are just "no"."""
    try:
        _hasher(settings).verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def validate_password_policy(settings: Settings, password: str) -> None:
    """Raises :class:`AuthError` with an explanation if `password` is too weak
    to accept. Length only (NIST 800-63B) -- see config.py's docstring on
    `auth_password_min_length` for why no composition rules are layered on
    top."""
    if len(password) < settings.auth_password_min_length:
        raise AuthError(
            f"password must be at least {settings.auth_password_min_length} characters"
        )


# ---------------------------------------------------------------------------
# Opaque tokens (sessions and API tokens share this shape)
# ---------------------------------------------------------------------------


def generate_token(*, prefix: str = "") -> str:
    """A high-entropy opaque token. 32 bytes -> 256 bits before base64url
    encoding, comfortably beyond anything worth brute-forcing."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


def _as_utc(value: datetime) -> datetime:
    """SQLite (the developer/on-device profile, ADR-007) has no native
    timezone-aware storage: a `DateTime(timezone=True)` column round-trips
    as a naive `datetime` even though every value written was UTC. Treat a
    naive value as UTC rather than let it blow up the comparison against
    `datetime.now(UTC)` below -- this mirrors `api/app.py`'s own `_iso()`
    helper, which makes exactly the same assumption for the same reason."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def hash_token(token: str) -> str:
    """SHA-256 of a token, for at-rest storage and lookup.

    Deliberately *not* a slow KDF like Argon2: the token already carries
    ~256 bits of entropy (unlike a human password, which needs the KDF's
    cost to make guessing expensive), and this hash is computed on every
    authenticated request, where an Argon2id cost of ~3 iterations x 64 MiB
    would be a real latency and CPU cost paid on every API call.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """A coarse in-memory sliding-window limiter, keyed by an arbitrary string
    (the login endpoint uses the client's remote address).

    Deliberately not backed by Redis or any shared store: this is a single
    appliance process, per ADR-034's reasoning, and the failure mode of an
    in-memory limiter (it resets on restart, and would under-count behind a
    multi-worker deployment this project does not run) is judged acceptable
    for a login form on a home LAN. A note documenting that trade-off is not
    optional -- see ADR-034.
    """

    def __init__(self, max_attempts: int, window_s: float) -> None:
        self._max_attempts = max_attempts
        self._window_s = window_s
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> tuple[bool, float]:
        """Records an attempt for `key` and returns `(allowed, retry_after_s)`.

        A disallowed attempt is still recorded as a hit (in the window),
        so a caller hammering the endpoint does not get a rolling free pass.
        """
        now = time.monotonic()
        recent = [t for t in self._hits.get(key, []) if now - t < self._window_s]
        allowed = len(recent) < self._max_attempts
        recent.append(now)
        self._hits[key] = recent
        if allowed:
            return True, 0.0
        retry_after = self._window_s - (now - recent[0])
        return False, max(0.0, retry_after)

    def reset(self, key: str) -> None:
        """Clear a key's history -- called on a successful login so a
        legitimate operator who mistyped a password twice is not left
        throttled for the rest of the window."""
        self._hits.pop(key, None)

    def sweep(self, *, max_age_s: float = 3600.0) -> None:
        """Drop keys with no recent activity, so a long-running process does
        not accumulate one dict entry per distinct client forever. Cheap
        enough to call opportunistically (the login endpoint does, on every
        call) rather than needing its own scheduled task."""
        now = time.monotonic()
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] > max_age_s]
        for key in stale:
            self._hits.pop(key, None)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AuthMetrics:
    """Counters for auth outcomes, in their own registry so `/metrics`
    concatenation (matching the MQTT publisher's `render_metrics` precedent)
    never collides with the station's own registry."""

    def __init__(self) -> None:
        self._registry = CollectorRegistry()
        self.login_success = Counter(
            "oo_auth_login_success_total", "Successful logins", registry=self._registry
        )
        self.login_failure = Counter(
            "oo_auth_login_failure_total", "Failed login attempts", registry=self._registry
        )
        self.login_rate_limited = Counter(
            "oo_auth_login_rate_limited_total",
            "Login attempts rejected by the rate limiter",
            registry=self._registry,
        )
        self.session_rejected = Counter(
            "oo_auth_session_rejected_total",
            "Requests with an invalid, expired or revoked session cookie",
            registry=self._registry,
        )
        self.token_rejected = Counter(
            "oo_auth_token_rejected_total",
            "Requests with an invalid or revoked API token",
            registry=self._registry,
        )
        self.enabled_gauge = Gauge(
            "oo_auth_enabled", "1 when authentication is enforced", registry=self._registry
        )

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self._registry), CONTENT_TYPE_LATEST


class AuthService:
    """Everything the API layer needs: bootstrap, login, session/token
    validation, and revocation. Holds no long-lived database session of its
    own -- every method takes one, exactly like the rest of this codebase's
    request-scoped `Session` usage.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.metrics = AuthMetrics()
        self.metrics.enabled_gauge.set(1.0 if settings.auth_enabled else 0.0)
        self.login_limiter = RateLimiter(
            settings.auth_login_rate_limit_attempts, settings.auth_login_rate_limit_window_s
        )

    # -- bootstrap ----------------------------------------------------------

    def bootstrap_admin_if_needed(self, session: Session) -> str | None:
        """Create the first account if none exists. Returns the generated
        plaintext password exactly once (for the caller to print/log), or
        `None` if a user already exists and nothing was done.

        Never ships a default password: it is generated fresh with
        :func:`secrets.token_urlsafe` every time this runs against an empty
        `user` table, so a station is never reachable with a value anyone
        could have looked up in this repository. The account is created with
        `must_change_password=True`, so the generated value cannot become a
        de facto permanent password by an operator simply never changing it
        -- the first login forces the point.
        """
        existing = session.execute(select(func.count(orm.User.id))).scalar_one()
        if existing:
            return None
        password = secrets.token_urlsafe(18)
        user = orm.User(
            username=self.settings.auth_bootstrap_username,
            password_hash=hash_password(self.settings, password),
            must_change_password=True,
        )
        session.add(user)
        session.commit()
        log.warning(
            "auth.bootstrap_account_created",
            username=user.username,
            note="generated password printed to stdout/log once; change it on first login",
        )
        return password

    # -- login / logout -------------------------------------------------

    def authenticate(self, session: Session, *, username: str, password: str) -> orm.User:
        """Raises :class:`AuthError` on any failure. Deliberately the same
        message for "no such user" and "wrong password" -- distinguishing
        them lets an attacker enumerate valid usernames."""
        user = session.execute(select(orm.User).where(orm.User.username == username)).scalar_one_or_none()
        if user is None or user.disabled_at is not None:
            # Still runs a hash so the response-time difference between "no
            # such user" and "wrong password" is not itself a timing oracle.
            _hasher(self.settings).hash(secrets.token_urlsafe(8))
            raise AuthError("invalid username or password")
        if not verify_password(self.settings, password, user.password_hash):
            raise AuthError("invalid username or password")
        user.last_login_at = datetime.now(UTC)
        session.commit()
        return user

    def create_session(
        self, session: Session, *, user: orm.User, user_agent: str = ""
    ) -> tuple[str, orm.AuthSession]:
        """Returns `(raw_token, row)`. The raw token is what goes in the
        cookie; only its hash is persisted."""
        token = generate_token()
        row = orm.AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=self.settings.auth_session_ttl_hours),
            user_agent=user_agent[:300],
        )
        session.add(row)
        session.commit()
        return token, row

    def resolve_session(self, session: Session, raw_token: str) -> Principal | None:
        row = session.execute(
            select(orm.AuthSession).where(orm.AuthSession.token_hash == hash_token(raw_token))
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None or _as_utc(row.expires_at) <= datetime.now(UTC):
            self.metrics.session_rejected.inc()
            return None
        user = session.get(orm.User, row.user_id)
        if user is None or user.disabled_at is not None:
            self.metrics.session_rejected.inc()
            return None
        return Principal(
            user_id=user.id,
            username=user.username,
            method="session",
            must_change_password=user.must_change_password,
        )

    def revoke_session(self, session: Session, raw_token: str) -> None:
        row = session.execute(
            select(orm.AuthSession).where(orm.AuthSession.token_hash == hash_token(raw_token))
        ).scalar_one_or_none()
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            session.commit()

    # -- password change --------------------------------------------------

    def change_password(
        self, session: Session, *, user: orm.User, current_password: str, new_password: str
    ) -> None:
        if not verify_password(self.settings, current_password, user.password_hash):
            raise AuthError("current password is incorrect")
        validate_password_policy(self.settings, new_password)
        user.password_hash = hash_password(self.settings, new_password)
        user.must_change_password = False
        session.commit()
        log.info("auth.password_changed", username=user.username)

    # -- API tokens -----------------------------------------------------

    def create_api_token(
        self, session: Session, *, user: orm.User, name: str
    ) -> tuple[str, orm.ApiToken]:
        """Returns `(raw_token, row)`. Exactly like a session token, the raw
        value is shown to the caller once and never persisted or logged
        again -- see the module docstring."""
        token = generate_token(prefix=API_TOKEN_PREFIX)
        row = orm.ApiToken(
            user_id=user.id,
            name=name[:120] or "unnamed token",
            token_prefix=token[:_TOKEN_LOOKUP_PREFIX_LEN],
            token_hash=hash_token(token),
        )
        session.add(row)
        session.commit()
        return token, row

    def resolve_api_token(self, session: Session, raw_token: str) -> Principal | None:
        if not raw_token.startswith(API_TOKEN_PREFIX):
            return None
        row = session.execute(
            select(orm.ApiToken).where(orm.ApiToken.token_hash == hash_token(raw_token))
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            self.metrics.token_rejected.inc()
            return None
        user = session.get(orm.User, row.user_id)
        if user is None or user.disabled_at is not None:
            self.metrics.token_rejected.inc()
            return None
        row.last_used_at = datetime.now(UTC)
        session.commit()
        return Principal(user_id=user.id, username=user.username, method="token")

    def revoke_api_token(self, session: Session, *, user: orm.User, token_id: uuid.UUID) -> bool:
        row = session.get(orm.ApiToken, token_id)
        if row is None or row.user_id != user.id:
            return False
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            session.commit()
        return True

    def list_api_tokens(self, session: Session, *, user: orm.User) -> list[orm.ApiToken]:
        return list(
            session.execute(
                select(orm.ApiToken)
                .where(orm.ApiToken.user_id == user.id)
                .order_by(orm.ApiToken.created_at.desc())
            )
            .scalars()
            .all()
        )

    # -- health / observability -----------------------------------------

    def active_user_count(self, session: Session) -> int:
        return session.execute(
            select(func.count(orm.User.id)).where(orm.User.disabled_at.is_(None))
        ).scalar_one()


__all__ = [
    "API_TOKEN_PREFIX",
    "AuthError",
    "AuthMetrics",
    "AuthService",
    "Principal",
    "RateLimiter",
    "generate_token",
    "hash_password",
    "hash_token",
    "validate_password_policy",
    "verify_password",
]
