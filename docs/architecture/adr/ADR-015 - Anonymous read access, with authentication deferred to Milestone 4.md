# ADR-015: Anonymous read access, with authentication deferred to Milestone 4
**Decision:** The debug slice serves the API and UI with no authentication and anonymous
read enabled, on a trusted LAN. This knowingly contradicts `TECHNICAL_SPEC.md` §9, which
requires anonymous read access disabled by default in the first release.

**Reason:** The debug slice's purpose is measurement on real hardware, and an auth layer
would have added a failure surface to every diagnostic without making any measurement more
truthful. Milestone 4 owns the authentication foundation.

**Constraint:** This is a deviation with a real security consequence, so it is recorded
rather than assumed: the station must not be exposed beyond the local network until
Milestone 4 lands, and station coordinates are readable by anyone who can reach the port.
Milestone 4 cannot be called complete while this ADR stands.

> **Status 2026-08-08: CLOSED by ADR-034.** An authentication foundation shipped —
> Argon2id passwords, session cookies, revocable API tokens. It is deliberately
> **off by default** (`auth_enabled=false`), so a station that has not opted in
> still behaves exactly as this ADR describes, and the constraint above still
> applies to it. ADR-034 also records one deliberate exemption: with auth enabled,
> `GET /api/v1/detections`, `GET /api/v1/health` and `/metrics` stay reachable with
> no credential, for the ESP32 display and `deploy.sh`.
