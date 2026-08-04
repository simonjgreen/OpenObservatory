# API and Integration Specification

## REST base

`/api/v1`

### Key endpoints

- `GET /station`
- `GET /health`
- `GET /audio/devices`
- `POST /audio/probe`
- `POST /audio/test-capture`
- `GET /streams`
- `GET /gaps`
- `GET /detectors`
- `POST /detectors/{id}/enable`
- `POST /detectors/{id}/disable`
- `GET /detections`
- `GET /detections/{id}`
- `POST /detections/{id}/reviews`
- `GET /detections/{id}/media`
- `GET /taxa/activity`
- `GET /telemetry`
- `GET /alerts/rules`
- `POST /alerts/rules`
- `POST /exports`
- `GET /exports/{id}`

Use cursor pagination. Filters must be explicit and documented. OpenAPI is generated and checked into release artefacts.

## Event stream

SSE is sufficient for v1 dashboard updates; WebSocket may be added for richer controls.

Event envelope:

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "event_type": "detection.created",
  "occurred_at": "2026-08-04T17:00:00Z",
  "station_id": "uuid",
  "data": {}
}
```

## MQTT

Default root: `openobservatory/{station_id}`

Topics:

- `status/availability` retained
- `status/capture` retained
- `status/detectors/{detector_id}` retained
- `metrics/species_today` retained
- `metrics/capture_continuity` retained
- `detection` non-retained JSON
- `alert` non-retained JSON
- `health/event` non-retained JSON

Telemetry input mappings are configured as topic + JSONPath/value/unit mappings.

## Home Assistant discovery

Publish discovery entities for:

- station availability;
- capture state;
- uninterrupted capture duration;
- last species;
- last detection time;
- species count today;
- disk free;
- microphone level/clipping;
- each detector state and lag;
- high-severity health event count.

Do not create one permanent entity per detected species by default; this becomes unwieldy.

## MCP server

Expose concise, bounded tools:

- `get_station_status`
- `list_recent_detections`
- `summarise_activity`
- `compare_periods`
- `get_species_history`
- `get_detection_evidence_metadata`
- `list_capture_gaps`
- `list_health_events`
- optional `review_detection`

MCP tools return structured data and never embed raw audio. Media is represented by an authenticated local URL or asset ID. Queries must enforce maximum date ranges and result counts.

## Webhooks

Outgoing webhook payload uses the common event envelope, signed with HMAC SHA-256. Retries are bounded with exponential backoff and dead-letter reporting.
