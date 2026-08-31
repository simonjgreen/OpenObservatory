---
aliases:
  - ADR-034
tags:
  - adr
---
# ADR-034: An authentication foundation closes ADR-015, deliberately off by default, with one exemption for a display that cannot be reflashed
**Status:** active. Its "no Alembic migration is written here" note is closed by
revision `0003_auth_tables`, recorded in the addendum at the end. Two things it
states are no longer current: `auth_public_read_paths` defaults to three paths
rather than one, and the title's premise — a display that cannot be reflashed —
was overtaken by [[ADR-050 - Display OTA slots|ADR-050]]'s OTA slots, though the exemption itself was kept
deliberately. See the 2026-08-29 note at the end.

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
running this display. The second option effectively vetoes closing [[ADR-015 - Anonymous read, auth deferred|ADR-015]]
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
(`model/settings.h`) that are unused today ([[ADR-023 - The ESP32 inside observer|ADR-023]] shipped HTTP polling
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
plain HTTP with no TLS component anywhere in this codebase ([[ADR-015 - Anonymous read, auth deferred|ADR-015]]'s
LAN-trust premise is unchanged, not revisited here). A cookie marked `Secure`
is refused by the browser on a non-HTTPS origin — not "less safe", simply
never sent — which would make `POST /api/v1/auth/login` appear to succeed
(200, a `Set-Cookie` header goes out) while every subsequent request silently
carries no credential at all. That is strictly worse than the status quo: a
login page that appears to work and does not is a worse trap than no login
page. `auth_cookie_secure` defaults to `false` and is documented, in both
this ADR and [[DEPLOYMENT_AND_OPERATIONS]], as something to
flip to `true` only once a reverse proxy or similar terminates TLS in front
of this station — at which point the browser's own protections start doing
real work. `HttpOnly` and `SameSite=Lax` are unconditional regardless of
`auth_cookie_secure`, since both are free of that HTTP-only failure mode and
narrow real attack surface (script-readable cookie theft; naive
cross-site-request forgery) independent of TLS.

**What this protects against, and what it explicitly does not — say this
plainly rather than claim "secure".** It protects against another device or
person on the same LAN reading or changing station state with zero
credential, which is the exact gap [[ADR-015 - Anonymous read, auth deferred|ADR-015]] recorded. It does **not**
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
SQLite developer/on-device profile ([[ADR-007 - SQLite in developer mode|ADR-007]]); the PostgreSQL profile will
need these three tables added to whatever Alembic revision that agent's work
produces.

> **Status 2026-08-08: that follow-up landed.** Revision
> `0003_auth_tables` adds `user`, `auth_session` and `api_token`. The live
> station reports `0003_auth_tables (head)`.

**Reviewed 2026-08-29:** the decision holds — `auth_enabled` still defaults to
`false` (`src/open_observatory/config.py:521`), the blanket middleware gate is
still the enforcement point (`api/app.py:456`), and the live station reports
`"auth": {"enabled": false}` from `GET /api/v1/health`. Three references above
have gone stale. `auth_public_read_paths` now defaults to three paths, not one:
`/api/v1/detections`, `/api/v1/display` and `/api/v1/firmware/image`
(`config.py:552`), widened by [[ADR-038 - Display push channel|ADR-038]]'s WebSocket push channel — whose
handler consults this same list rather than the HTTP gate (`api/app.py:2612`) —
and by [[ADR-050 - Display OTA slots|ADR-050]]'s OTA image fetch. The hardcoded always-public set is
`/api/v1/health`, `/api/v1/auth/login` and `/api/v1/auth/logout`
(`api/app.py:146`); the login and logout pair was there from the first commit
and is simply not mentioned above. And `argon2-cffi` is pinned at `25.1.0`
(`pyproject.toml:45`), not `23.1.0`; the Argon2id cost parameters in
`config.py` are unchanged.

**Reviewed 2026-08-29 — the exemption outlived its stated premise.** [[ADR-050 - Display OTA slots|ADR-050]]
gave the display two OTA slots, so it can be updated from the station without a
USB reflash; the condition this ADR's title rests on no longer holds. The
exemption was kept anyway, for a reason recorded in `config.py` rather than
here: an ESP32 with no keyboard cannot log in, and the OTA image path in
particular must stay public because an update path that only works while
authentication is off stops working on the day it matters. So the claim above
that `auth_public_read_paths` "can be set to `()`" once the firmware carries a
bearer token no longer describes the whole state: the firmware still sends no
`Authorization` header (nothing under `firmware/inside-observer/` matches it, so
the follow-up is still unbuilt), and clearing the list outright would now also
disable OTA.

**Reviewed 2026-08-30: the regression test named for this risk covers three of
the five credential-free paths, not five.** The Verification paragraph above
says "the ESP32-dependent paths ... stay reachable under the gate by a dedicated
regression test named for exactly that risk."
`tests/test_auth.py::TestAuthEnabledBlanketGate::test_esp32_counter_top_display_endpoints_stay_public`
asserts `/api/v1/health`, `GET /api/v1/detections` and `/metrics`, and its
docstring still says "these two paths". It does not touch `/api/v1/display` —
[[ADR-038 - Display push channel|ADR-038]]'s push channel, which has been the display's *primary* transport
since 2026-08-09 — and it does not touch `/api/v1/firmware/image`. No test in
this repository opens either with `auth_enabled` true. No test anywhere asserts
a 4401 close: all three WebSocket gates (`/api/v1/live`, `/api/v1/live/audio`,
`/api/v1/display`) have no automated coverage at all, and the 4401 behaviour
recorded above was seen once in a browser and never since. `test_api.py`'s
`test_keep_is_not_a_public_read_path` asserts only that `/api/v1/detections` is
in the default list, so deleting either of the other two entries from
`auth_public_read_paths` would fail no test and take the display's socket or its
update path down silently.

**Reviewed 2026-08-30: the exemption is one path short of what the display
needs.** Building the real application with `auth_enabled: true` and requesting
every path the display uses, with no credential: on the shipped three-path
default, `/api/v1/health`, `GET /api/v1/detections`, `/metrics`,
`WS /api/v1/display` and `GET /api/v1/firmware/image` all answer, and
`GET /api/v1/history?window=today` returns **401**. The inside-observer's HTTP
fallback fetches that path (`firmware/inside-observer/src/station_source.cpp`)
for its species-today count and for the station's real UTC offset, and treats
the failure as non-fatal — so the fallback screen keeps a stale count and drops
back to the firmware's configured offset constant. The exemption therefore does
not fully hold even at its own defaults; it holds for the push channel and
breaks a corner of the fallback. Recorded, not fixed: this ADR's territory does
not extend to changing the default. The same run confirms what
`config/example.env`'s one-path value costs — `WS /api/v1/display` closed with
4401 and `GET /api/v1/firmware/image` 401 — which [[DEPLOYMENT_AND_OPERATIONS]]
now describes for an operator.

**Reviewed 2026-08-30: still never exercised on a station.** `GET
/api/v1/health` reports `auth: {"enabled": false}` on the live station, as at
every review since this ADR landed on 2026-08-08, and nothing in `results/` or
the delivery documents records `auth_enabled: true` ever having run on the Pi.
The whole of this feature's evidence is 27 cases in `tests/test_auth.py` (the
count above says 26; it has grown by one), 19 web cases, and the one manual
Chromium session described under Verification, which ran against a developer
machine. Two smaller corrections: the line numbers in the first 2026-08-29 note
(`config.py:521`, `config.py:552`, `api/app.py:2612`) no longer resolve — the
anchors are the `auth_enabled` and `auth_public_read_paths` field definitions in
`config.py` and the `display_socket` handler's allow-list check in
`api/app.py`, and no line number is quoted here because those files move under
one. `api/app.py:146` (`_ALWAYS_PUBLIC_PATHS`) and `api/app.py`'s
`_enforce_auth` middleware are still where that note says they are.

---
Part of the [[ADRS|Architecture Decision Record index]].
