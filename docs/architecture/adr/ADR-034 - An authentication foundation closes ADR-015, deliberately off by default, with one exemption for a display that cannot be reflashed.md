# ADR-034: An authentication foundation closes ADR-015, deliberately off by default, with one exemption for a display that cannot be reflashed
**Decision:** Local operator accounts (Argon2id-hashed passwords), `HttpOnly`
session cookies for the browser UI, and revocable, hashed, long-lived API
tokens for machine clients. A single `auth_enabled` setting (default **off**)
gates enforcement; a configurable `auth_public_read_paths` allow-list
(default: `/api/v1/detections`, GET only) plus two hardcoded always-public
paths (`/api/v1/health`, and `/metrics` — which never matches the gate's
`/api/v1/*` prefix at all) stay reachable with no credential regardless of
that setting. Implemented in `src/open_observatory/auth.py` (the service:
hashing, tokens, sessions, rate limiting), `db/models.py` (`User`,
`AuthSession`, `ApiToken`), the auth section of `config.py`, and wired
through `api/app.py` as a blanket Starlette middleware plus
`/api/v1/auth/{login,logout,me,password,tokens}`. The web UI gets a login
view, a forced-password-change view for the bootstrap account, and honest
401 handling (`web/src/api.ts`, `hooks/useAuth.ts`, `state/authState.ts`,
`components/Login.tsx`).

**Reason this is a foundation, not an IAM system.** This is a single-operator
LAN appliance (CLAUDE.md forbids requiring cloud connectivity for core
capture/detection/review/query), not a multi-tenant product. There are no
roles, no groups, no org model, no password reset flow (an operator who loses
their password regains access by clearing the `user` table or disabling
`auth_enabled` and creating a fresh one — undignified, but honest about what
a one-account local appliance needs). Session tokens and API tokens are
stored only as SHA-256 hashes (fast, deliberately not Argon2 — the token
already carries ~256 bits of entropy from `secrets.token_urlsafe`, and this
hash runs on every authenticated request, where an Argon2id cost would be a
real per-request tax); passwords are Argon2id (`argon2-cffi==23.1.0`, cost
parameters pinned in `config.py` rather than left at the library's shipped
default, exactly like every other dependency in this project) and are never
logged — `AuthService.authenticate`'s failure path is covered by a test that
asserts the plaintext never reaches a log call.

**Reason for defaulting `auth_enabled` to off.** CLAUDE.md: "Add structured
logs, metrics, health checks and graceful degradation with every service" —
and the single worst outcome this feature could produce is an operator
running `git pull && systemctl restart` and finding their own station
unreachable, with no way back in short of SSH and a database edit, because a
new default silently started requiring a login nobody set up. Off-by-default
means an upgrade changes nothing until the operator deliberately opts in.
The gap this leaves is real and is not hidden: `/api/v1/health` never flags
`auth_enabled: false` as a `problems` entry (that would make a freshly
upgraded station report itself "degraded" out of the box, for doing exactly
what it has always done), but a structured warning
(`auth.disabled`) is logged once at every startup, and `GET
/api/v1/health`'s `auth` object always reports `{"enabled": false}` plainly
rather than omitting the key. What **is** a `problems` entry, because it is a
genuine lockout rather than an unconfigured default: `auth_enabled: true`
with zero active user accounts (`active_users: 0`) — a state that should be
unreachable in normal operation (bootstrap always creates one) but is
surfaced loudly if an operator's own account-management ever produces it,
per CLAUDE.md's "graceful degradation ... with every service."

**Reason for the ESP32 exemption, stated as a trade-off rather than papered
over.** `firmware/inside-observer` polls `GET /api/v1/detections` and
`GET /api/v1/health` every `pollSeconds` (default 20s) with no way to carry a
credential — it is a 4 MB-flash ESP32 with no PSRAM, its HTTP client sends a
bare GET, and this agent's territory explicitly excludes `firmware/`, so
adding bearer-token support there is not a same-session option; doing it
anyway would need a physical USB reflash of a device sitting on the
operator's counter top. Two paths were available: leave those two endpoints
reachable with no credential even when `auth_enabled` is true (chosen), or
require a reflash before `auth_enabled` could ever be turned on for a station
running this display. The second option effectively vetoes closing ADR-015
at all for the one station this session has real telemetry from, which is
worse than a scoped, documented exemption. **What the exemption actually
costs:** with `auth_enabled: true`, anything on the LAN can still read the
station's recent detections (species, timestamps, scores) and coarse health
without a credential — not station coordinates or clip audio (`/api/v1/media`
and the export/history/token/review endpoints are all gated normally), but a
real reduction from "everything requires a login." `/api/v1/health` is
additionally hardcoded (not merely defaulted) into the always-public set,
independent of `auth_public_read_paths`, because `deploy/deploy.sh` polls it
after every restart with no credential and no login flow of its own — making
it authenticate-only would turn every future deploy into a hang.
**Follow-up, not built here:** `firmware/inside-observer` already has an
NVS-persisted `MqttSettings` struct with `username`/`password` fields
(`model/settings.h`) that are unused today (ADR-023 shipped HTTP polling
first, MQTT is "not wired up yet"); the natural home for a future bearer
token is a new field alongside them, sent as `Authorization: Bearer <token>`
on both polled requests, provisioned through the same captive-portal config
page the MQTT fields already use. Once that firmware update ships and is
deployed, `auth_public_read_paths` can be set to `()` (or the operator can
simply stop configuring it) and the exemption closes completely without any
further backend change — the allow-list is already the only thing standing
between "today" and "closed."

**Reason `Secure` is off by default on the session cookie, stated honestly
rather than quietly making login not work.** The station is served over
plain HTTP with no TLS component anywhere in this codebase (ADR-015's
LAN-trust premise is unchanged, not revisited here). A cookie marked `Secure`
is refused by the browser on a non-HTTPS origin — not "less safe", simply
never sent — which would make `POST /api/v1/auth/login` appear to succeed
(200, a `Set-Cookie` header goes out) while every subsequent request silently
carries no credential at all. That is strictly worse than the status quo: a
login page that appears to work and does not is a worse trap than no login
page. `auth_cookie_secure` defaults to `false` and is documented, in both
this ADR and `docs/operations/DEPLOYMENT_AND_OPERATIONS.md`, as something to
flip to `true` only once a reverse proxy or similar terminates TLS in front
of this station — at which point the browser's own protections start doing
real work. `HttpOnly` and `SameSite=Lax` are unconditional regardless of
`auth_cookie_secure`, since both are free of that HTTP-only failure mode and
narrow real attack surface (script-readable cookie theft; naive
cross-site-request forgery) independent of TLS.

**What this protects against, and what it explicitly does not — say this
plainly rather than claim "secure".** It protects against another device or
person on the same LAN reading or changing station state with zero
credential, which is the exact gap ADR-015 recorded. It does **not**
protect the session cookie or a bearer token from anything that can observe
LAN traffic (no TLS exists in this codebase to prevent that), does not
protect against a compromised device that already has a valid credential
(no device binding, no anomaly detection), does not implement CSRF tokens
(mitigated only by `SameSite=Lax` plus this API's near-total lack of
state-changing `GET` routes — a narrower guarantee than a dedicated token),
and does not rate-limit anything except the login endpoint itself (an
authenticated client can still call any other endpoint as fast as it likes,
unchanged from before this feature). The rate limiter itself is coarse and
in-process (`auth.RateLimiter`): it resets on restart and does not share
state across workers, which is judged acceptable for a login form on a
single-appliance home LAN process (this project runs one worker) but would
under-count in front of a load balancer this project does not run.

**Bootstrap.** On first startup with `auth_enabled: true` and an empty `user`
table, one account (`auth_bootstrap_username`, default `operator`) is created
with a `secrets.token_urlsafe(18)`-generated password, printed once to
stdout (`flush=True` — block-buffering under uvicorn/systemd was observed
during this work to hold the banner in an ~8 KiB pipe buffer indefinitely
without it) and logged at WARNING, with `must_change_password: true`. The
generated password is never the default in code, config, or documentation —
grep this repository for it and find nothing, by construction. The web UI
enforces the change-password step before showing the rest of the app; the
API enforces it structurally the same way any other principal's
`must_change_password` flag would be honoured by a client that checks it
(the flag is returned from `/auth/login` and `/auth/me`, but is **not**
itself enforced as a second gate on other endpoints in this change — an
operator who ignores the UI's prompt and calls the API directly with a
machine client stays logged in on the generated password. Recorded as a
scope gap rather than silently claimed closed.)

**Verification.** `tests/test_auth.py` (26 cases): hashing never returns or
logs plaintext; sessions and tokens round-trip and reject forged/expired/
revoked credentials; the blanket gate is a no-op with `auth_enabled: false`
and refuses reads and writes alike once true; the ESP32-dependent paths
(`/api/v1/health`, `/api/v1/detections` GET, `/metrics`) stay reachable
under the gate by a dedicated regression test named for exactly that risk;
the login endpoint rate-limits and returns `Retry-After`; a locked-out
station (`auth_enabled: true`, zero active users) is flagged in `/health`.
`web/src/state/authState.test.ts`, `hooks/useAuth.test.tsx`, and
`components/Login.test.tsx` (19 cases) cover the client-side state machine,
including that a stray 401 during the initial `/me` probe cannot flash a
login form on a station where auth turns out to be off. The full login →
forced-password-change → authenticated dashboard → sign-out → re-login cycle
was additionally driven through a real Chromium instance against a locally
running `oo serve` (synthetic source, `OO_AUTH_ENABLED=true`) during this
work, screenshots retained in the session transcript, confirming the cookie,
the WebSocket's own auth check (`/api/v1/live` closes with code 4401 and no
credential once enabled, verified by the same session going dark on logout
until sign-in), and the UI's gating all agree with each other. Not run: the
72-hour soak, and anything against the live Pi station, per this session's
hard rule against touching it.

**Territory note for reconciliation.** Two new tables (`user`,
`auth_session`) and one (`api_token`) are added to `db/models.py`. No
Alembic migration is written here — `alembic/` and `db/session.py` are a
concurrent agent's territory this session — but SQLite's existing
`create_all()` + `_patch_sqlite_columns()` path in `db/session.py` already
creates any new table on next startup with no migration needed for the
SQLite developer/on-device profile (ADR-007); the PostgreSQL profile will
need these three tables added to whatever Alembic revision that agent's work
produces.

> **Status 2026-08-08: that follow-up landed.** Revision
> `0003_auth_tables` adds `user`, `auth_session` and `api_token`. The live
> station reports `0003_auth_tables (head)`.
