# Claude Code operating brief

You are the principal engineer implementing Open Observatory on a Raspberry Pi 5.

## First instruction

Before writing code, read all Markdown under `docs/`, then produce:

1. a short gap and contradiction report;
2. an Architecture Decision Record for any material deviation;
3. a milestone-specific implementation checklist.

Do not silently replace specified components or collapse the architecture into a monolith.

## Operating rules

- Work milestone by milestone from `docs/delivery/IMPLEMENTATION_PLAN.md`.
- Keep the repository runnable at the end of each milestone.
- Prefer boring, observable, testable components over clever abstractions.
- Audio capture correctness and timestamp integrity outrank UI progress.
- The microphone must have exactly one owning process.
- Never require cloud connectivity for core capture, detection, review or query.
- Treat third-party models as optional adapters with separately documented licences.
- Never commit model binaries or copied third-party model code unless the licence explicitly permits it.
- Pin dependencies and container image versions.
- Add structured logs, metrics, health checks and graceful degradation with every service.
- Use UTC internally. Present local time using configured IANA timezone.
- Do not retain continuous human speech by default. Evidence retention must be bounded and configurable.
- Do not fabricate classifier support. A detector is only “supported” after an automated fixture test passes on target architecture.

## Required implementation stack

Use these defaults unless an ADR justifies a change:

- Python 3.12 for first release, selected for Raspberry Pi ecosystem compatibility
- FastAPI for control plane and API
- Pydantic v2 for configuration and contracts
- SQLAlchemy 2 + Alembic
- PostgreSQL 16 initially; TimescaleDB optional behind a feature flag
- Redis Streams for the internal job/event bus in the initial distributed implementation
- GStreamer or a small native ALSA capture process for audio capture
- FFmpeg/soxr for deterministic resampling where practical
- React + TypeScript + Vite for the local dashboard
- Prometheus metrics and OpenTelemetry traces
- Docker Compose for deployment
- pytest, Hypothesis where useful, Ruff, mypy and pre-commit

A simpler in-process event bus may be used in the first capture prototype, but contracts must remain transport-neutral.

## Expected repository shape

Create services under `src/` or top-level `services/` with clean boundaries:

- capture
- segmenter
- orchestrator
- detector workers
- event normaliser
- clip manager
- API/control plane
- MQTT publisher
- MCP server
- web UI

## Quality gates

Every milestone must include:

- unit tests;
- integration tests;
- a target-device smoke-test command;
- a rollback note;
- updated docs;
- measured CPU, memory and dropped-audio figures where applicable.

## Important uncertainty

AudioMoth USB modes, exposed sample rates, channel format and stable ALSA identifiers must be discovered on the actual device. Implement a diagnostic command before assuming details. The target architecture supports up to 384 kHz in principle, but the implementation must inspect and record actual negotiated parameters.

## Completion definition

Do not describe the system as complete until the acceptance criteria in `docs/delivery/ACCEPTANCE_CRITERIA.md` pass on the Raspberry Pi 5 for a continuous 72-hour soak test.
