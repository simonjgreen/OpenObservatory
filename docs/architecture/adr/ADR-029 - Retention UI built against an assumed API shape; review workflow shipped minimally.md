# ADR-029: Retention UI built against an assumed API shape; review workflow shipped minimally
**Decision, retention:** `RetentionPanel` calls `GET /api/v1/retention/status` against a
shape documented in the component's own header comment (tiers by age, bytes/clip counts
per tier, an `eligible_for_deletion` total, `disk_reclaim_threshold`, `dry_run`), matching
the tiering already decided for this session (0–7d native+audible, 7–30d audible-only,
30–90d first/best-per-species, 90d+ deleted; continuous oldest-first reclaim above 85%
disk). The endpoint does not exist yet — another agent owns the retention backend this
session — so the component fetches, and on any non-2xx or network failure degrades to "Not
available yet," rather than throwing or showing stale/fabricated numbers. No control to
trigger a run is exposed: the recorded decision was a `--dry-run` **CLI** flag, an
operational affordance, not a button that could be mis-clicked on an always-on station
display.

**Decision, review:** the `review` table existed with nothing writing to it. This adds the
minimal round trip — `POST /api/v1/detections/{id}/review` (body: `status` ∈
`{confirmed, rejected}`, optional `note`) and `GET .../review` for the latest state —
wired to two buttons in `DetectionDrawer`. Every call **inserts**, matching `orm.Review`'s
own docstring ("append-only; current status is derived from the latest valid review");
`supersedes_review_id` is set to the prior row when one exists. `corrected_taxon_id` is
always written `None` — correcting a misidentified taxon is a materially different feature
(it implies a re-training or re-labelling pipeline downstream) and is left for a future
ADR rather than half-built here.

**Reason both are minimal:** the brief ranked these below the design-system/state-
extraction/disclosure foundation work, and said so explicitly for retention ("do NOT build
the retention backend... leave a clean seam") and implicitly for review ("lower priority
... if you have capacity"). Both are real, tested, working code — not stubs — scoped
tightly to what a seam and a minimal workflow require.

**Confirm before relying on this:** the retention response shape above is this agent's
best-effort prediction from the recorded tiering decision, not a contract the other agent
agreed to. Whoever lands the retention backend should either match it or tell the UI's next
maintainer what actually shipped; `RetentionPanel`'s degrade-gracefully path means a shape
mismatch fails safe (shows "not available") rather than silently, but it will still need a
one-time reconciliation pass.
