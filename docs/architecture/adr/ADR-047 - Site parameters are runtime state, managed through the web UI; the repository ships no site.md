---
aliases:
  - ADR-047
tags:
  - adr
---
# ADR-047: Site parameters are runtime state, managed through the web UI; the repository ships no site
**Decision:** Anything true of exactly one installation — coordinates, place
names, LAN addresses, hostnames, account names, filesystem homes — is **site
state**, not repository content. It lives in untracked runtime configuration
(`config/runtime.env`, NVS on the ESP32) and is editable through the web UI's
settings page (`GET`/`PUT /api/v1/settings`, `site_settings.py`) or the
firmware's provisioning portal. The repository describes a *system*; a
deployment describes a *site*. Committed defaults must be universally
applicable, and where no universal value exists the default is **unset, and
the system says so** — never a plausible-looking value that is silently
somebody else's.

**Context.** This repository began as one garden's observatory and was
saturated with that garden: the operator's home coordinates at ~11 m precision
in docs and four test files, the station and broker LAN addresses in scripts,
docs, web tests and the counter-top display firmware, and the operator's username and
home directory in the systemd unit. Publishing the repository makes every one
of those a permanent public disclosure. The deeper problem is behavioural,
not cosmetic: a cloned station that silently inherited the original site's
coordinates would run BirdNET's range-based plausibility filtering against
*someone else's garden* — confidently wrong output, which is the charter's
honesty constraint violated by omission.

**Mechanism.**

- `site_settings.py` holds the whitelist of operator-editable settings in
  three explicit tiers: **live** (station name, timezone; MQTT, applied by
  restarting the publisher — the same code path a process restart takes, so
  there is no second reconfigure path to drift), **restart-pinned**
  (latitude/longitude: bound into the BirdNET range filter and the night
  schedule when detectors start, and deliberately never swapped under a
  running range model — the station row records the operator's declaration,
  `Station.applied_site` records what the detectors are actually using, and
  the API, `/api/v1/health` and the UI all report any difference as "saved,
  in force after restart"), and **never browser-editable** (auth, bind
  address, storage paths: each exclusion reasoned in the module).
- Persistence is `config/runtime.env` itself — gitignored, operator-owned,
  written atomically with comments and unknown keys preserved, mode 0600. UI
  edits and hand edits are one configuration, not two.
- First-run honesty: latitude/longitude default to unset; `/api/v1/health`
  carries a `notes` list naming the consequence (no plausibility filtering,
  night schedule always-on), the station snapshot carries
  `location_configured`, and the web UI banners it with a link to settings.
  The default timezone is UTC — the only zone that is not somebody's local
  assumption. Empty env values for optional settings now mean "unset"
  (`OO_LATITUDE=`, as shipped by `config/example.env`, previously crashed
  startup).
- The inside-observer firmware ships no station address at all: an empty
  `stationHost` survives clamping, refuses to build a URL, and raises the
  existing provisioning portal, instead of a fresh unit silently polling one
  particular installation's LAN.
- Tests that need a location use a **neutral published reference** — the
  Royal Observatory, Greenwich (51.4769 N, 0.0005 W) — with every
  externally-derived expected value re-derived for it (sunrise-sunset.org
  civil twilight times; the BirdNET range model's Robin occurrence,
  re-measured at 0.8099 for week 30 with the real shipped model), never
  relabelled.

**For successors.** Do not hardcode a location, address or identity back in,
however convenient during debugging — not in a default, not in a test, not in
a doc example. Tests take locations as inputs or use the Greenwich reference;
doc examples use placeholder hosts (`<station-host>`) or RFC 5737 / RFC 2606
example addresses; historical measurement notes say "the development station"
rather than naming where it stands. If a new component needs a site
parameter, add it to the `site_settings.py` whitelist (choosing its tier
deliberately) rather than inventing a parallel mechanism.

### Rollback and smoke test (ADR-047)

No schema change, no new dependency. The settings endpoints and panel are
additive; revert the commits to remove them. Site values already present in a
station's `config/runtime.env` are untouched by either direction.

```bash
curl -s http://<station-host>:8080/api/v1/settings | python3 -m json.tool | head -30
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"latitude": 51.4769, "longitude": -0.0005}'
curl -s http://<station-host>:8080/api/v1/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["notes"])'
# expect the "restart required: latitude, longitude" note; restart, and expect it gone.
```

---
Part of the [[ADRS|Architecture Decision Record index]].
