---
aliases:
  - ADR-009
tags:
  - adr
---
# ADR-009: In-process event bus behind a transport-neutral protocol
**Status:** active.

**Decision:** Implement `EventBus` as an asyncio fan-out with bounded per-subscriber
queues and an explicit drop policy. Redis Streams becomes a second implementation of the
same protocol.

**Reason:** Explicitly permitted by `CLAUDE.md` for the first capture prototype. Bounded
queues and a recorded drop count keep the back-pressure behaviour that Redis Streams would
also have to provide, so the contract does not change when the transport does.

**Reviewed 2026-08-29:** the decision holds. `EventBus`
(`src/open_observatory/events.py:116`) is the asyncio fan-out described, with bounded
per-subscriber queues, a drop-*oldest* policy and a per-subscriber counter
(`events.py:88-103`); three subscribers use it — `live-ws` (256), `display-ws` (128,
detections only) and `mqtt` (`mqtt_queue_depth`, 256) — and their depths and drop counts
reach the station status snapshot under `bus` (`src/open_observatory/station.py:2308`).
Redis Streams remains documented rather than built: no `redis` client is a project
dependency in `pyproject.toml`, and the only Redis in the tree is the `redis:7-alpine`
service in the specification-level `docker-compose.yml`, which
[[ADR-008 - systemd, not Compose|ADR-008]] deferred and which has never run. Two
qualifications. "Protocol" in the title is a contract kept by hand, not a `typing.Protocol`;
every consumer annotates the concrete `EventBus`. And the overflow branch that produces the
drop count has no test of its own — the drop-oldest tests in `tests/test_display_channel.py`
cover `DisplayClient`, a different queue downstream of the bus. The part that would have to
give on a real transport is `publish()`, synchronous and non-blocking so the capture hot path
can call it (`events.py:142`), which no network transport can keep without buffering behind
it. Worth knowing before the swap; not a reason to change the decision.

---
Part of the [[ADRS|Architecture Decision Record index]].
