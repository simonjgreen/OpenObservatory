---
aliases:
  - ADR-008
tags:
  - adr
---
# ADR-008: Native systemd deployment for the debug slice; Compose deferred
**Status:** active; the "single `systemd` unit" is now two, since [[ADR-045 - Refinement runner|ADR-045]] gave the
refinement runner its own timer-driven unit.
**Decision:** Run the debug slice as a single `systemd` unit inside a virtualenv on the Pi,
with the web UI built to static assets and served by the API process. The Compose topology
of the technical spec is retained as the production target and is not deleted.

**Reason:** Only the capture process may own the ALSA device, and getting `/dev/snd`,
USB hot-plug re-enumeration and real-time scheduling right through a container adds a
failure surface with no benefit while the microphone is still absent. Native execution also
gives honest CPU/latency measurements uncontended by container overhead.

**Constraint:** service boundaries stay explicit in code — capture, segmenter, detector
workers, normaliser and API communicate only through the `EventBus` and window references,
never by reaching into each other's state. The single process is a *deployment* choice.

**Reviewed 2026-08-29:** the decision holds — `deploy/deploy.sh` still rsyncs into a
virtualenv, the UI is still built to `web/dist` and mounted by the API process
(`src/open_observatory/api/app.py:2752`), and `docker-compose.yml` is still present and
still unrunnable. The count changed: [[ADR-045 - Refinement runner|ADR-045]] moved the refinement runner into a second
unit, `deploy/open-observatory-refine.service`, fired nightly by
`open-observatory-refine.timer`, because [[ADR-033 - Retention is paced|ADR-033]] had measured that a thread inside the
capture process is not a fence. One venv, two units. The constraint above is unaffected —
that split is exactly the *deployment* choice it reserves.

---
Part of the [[ADRS|Architecture Decision Record index]].
