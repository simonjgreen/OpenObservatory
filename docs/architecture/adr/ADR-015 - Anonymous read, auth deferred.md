---
aliases:
  - ADR-015
tags:
  - adr
---
# ADR-015: Anonymous read access, with authentication deferred to Milestone 4
**Status:** closed by [[ADR-034 - Authentication foundation|ADR-034]] on 2026-08-08 — see the status blockquote below and its
2026-08-29 note, which corrects two things this ADR understated: the credential-free
exemption is three configurable paths plus a hardcoded set, and the anonymous exposure is
write as well as read.

**Decision:** The debug slice serves the API and UI with no authentication and anonymous
read enabled, on a trusted LAN. This knowingly contradicts [[TECHNICAL_SPEC]] §9, which
requires anonymous read access disabled by default in the first release.

**Reason:** The debug slice's purpose is measurement on real hardware, and an auth layer
would have added a failure surface to every diagnostic without making any measurement more
truthful. Milestone 4 owns the authentication foundation.

**Constraint:** This is a deviation with a real security consequence, so it is recorded
rather than assumed: the station must not be exposed beyond the local network until
Milestone 4 lands, and station coordinates are readable by anyone who can reach the port.
Milestone 4 cannot be called complete while this ADR stands.

> **Status 2026-08-08: CLOSED by [[ADR-034 - Authentication foundation|ADR-034]].** An authentication foundation shipped —
> Argon2id passwords, session cookies, revocable API tokens. It is deliberately
> **off by default** (`auth_enabled=false`), so a station that has not opted in
> still behaves exactly as this ADR describes, and the constraint above still
> applies to it. [[ADR-034 - Authentication foundation|ADR-034]] also records one deliberate exemption: with auth enabled,
> `GET /api/v1/detections`, `GET /api/v1/health` and `/metrics` stay reachable with
> no credential, for the ESP32 display and `deploy.sh`.

**Reviewed 2026-08-29:** the closure holds and the default has not moved —
`auth_enabled: bool = False` (`src/open_observatory/config.py:521`), Argon2id via
`argon2-cffi==25.1.0`, sessions and revocable API tokens in `src/open_observatory/auth.py`.
Two corrections to the note above, which was accurate when written and is no longer:

- **The exemption is no longer one path.** `auth_public_read_paths` now defaults to
  three — `/api/v1/detections`, `/api/v1/display` ([[ADR-038 - Display push channel|ADR-038]]'s push channel) and
  `/api/v1/firmware/image` ([[ADR-050 - Display OTA slots|ADR-050]]'s OTA image). [[ADR-050 - Display OTA slots|ADR-050]] also overtakes
  [[ADR-034 - Authentication foundation|ADR-034]]'s "a display that cannot be reflashed" premise: the display *can* now be
  reflashed, and the exemption was kept deliberately anyway, on the grounds that an
  update path which only works while authentication is off stops working on the day it
  matters. Separately hardcoded as always-public in `api/app.py:146` are
  `/api/v1/health`, `/api/v1/auth/login` and `/api/v1/auth/logout`; `/metrics` never
  matches `API_PREFIX` and so is never gated. [[TECHNICAL_SPEC]] §9's summary table
  and [[GAP_REPORT]] both say three; only [[ADR-034 - Authentication foundation|ADR-034]]'s
  Decision paragraph still reads one, corrected there by its own dated note.
- **The exposure is read *and* write.** The Constraint above says only that station
  coordinates are "readable". With `auth_enabled=false` anything on the LAN can also
  change station state — pause recording ([[ADR-055 - Timed recording pause|ADR-055]]), set site parameters
  ([[ADR-047 - The repository ships no site|ADR-047]]), set the `kept` flag ([[ADR-061 - Operator keep flag|ADR-061]]), trigger a firmware rollout
  ([[ADR-050 - Display OTA slots|ADR-050]]). [[ADR-034 - Authentication foundation|ADR-034]] states this correctly ("reading or changing station state
  with zero credential"); this ADR understates it.

Neither changes the decision. The constraint still binds every station that has not
opted in, which is still the default one.

---
Part of the [[ADRS|Architecture Decision Record index]].
