/** Shapes mirrored from the station's JSON. Kept explicit rather than `any`, so a
 *  server-side rename breaks the build instead of quietly blanking a panel. */

export interface SpectrogramSpec {
  channel: number
  name: string
  sample_rate: number
  bins: number
  min_hz: number
  max_hz: number
  hop_s: number
  fft_size: number
  floor_db: number
  ceiling_db: number
  /** Sent only in the `hello` frame: it never changes and is ~1.2 kB per channel.
   *  The frequency axis is derived from min_hz/max_hz, so this is provenance only. */
  centre_frequencies?: number[]
  columns_emitted: number
  history_columns: number
  /** Frames each column summarises. Exceeds fft_size where the hop is wider. */
  column_span_frames?: number
  /** Sub-windows max-combined per column, so no audio falls between columns. */
  sub_windows_per_column?: number
  /** True when the server only encodes this channel while a viewer is connected
   *  (ADR-040), which is what makes an empty canvas on connect normal rather
   *  than a symptom. Lets the UI explain the blank instead of implying failure. */
  viewer_gated?: boolean
  /** Seconds of history the server currently holds for backfill. Zero means a
   *  connecting client gets nothing and must fill from live columns. */
  history_seconds?: number
}

export interface LevelSample {
  frames: number
  sample_rate: number
  rms_dbfs: number
  peak_dbfs: number
  crest_factor_db: number
  clipped_samples: number
  clipping_ratio: number
  dc_offset: number
  silent: boolean
}

export interface CaptureStatus {
  state: string
  /** Human-readable state message. Stream provenance is `stream_detail`. */
  detail: string
  stream_detail: Record<string, unknown>
  stream_id: string | null
  source_kind: string | null
  is_live_hardware: boolean
  device_key: string | null
  device_label: string | null
  sample_rate: number | null
  sample_format: string | null
  channels: number | null
  started_utc: string | null
  blocks: number
  frames: number
  expected_frames: number | null
  continuity_ratio: number | null
  discontinuities: number
  estimated_missing_frames: number
  stream_restarts: number
  open_failures: number
  block_age_s: number | null
  hot_path_cpu_ratio: number | null
  observed_rate_hz: number | null
  rate_offset_ppm: number | null
  overruns: number | null
}

export interface RingStatus {
  sample_rate: number
  capacity_seconds: number
  held_seconds: number
  fill_ratio: number
  oldest_frame: number
  newest_frame: number
  chunks: number
  frames_written: number
  frames_evicted: number
  extractions: number
  extraction_misses: number
  extraction_partial: number
  estimated_bytes: number
}

export interface DetectorStatus {
  plugin_id: string
  plugin_version: string
  model_id: string
  model_version: string
  licence_name: string
  licence_url: string | null
  claim: string
  calibrated: boolean
  resource_class: string
  state: string
  detail: string
  window: { stream_kind: string; sample_rate: number; duration_s: number; stride_s: number }
  queue_depth: number
  queue_capacity: number
  windows_analysed: number
  windows_dropped_queue_full: number
  windows_dropped_stale: number
  detections_emitted: number
  failures: number
  last_runtime_ms: number | null
  p95_runtime_ms: number | null
  realtime_factor: number | null
  lag_s: number | null
  circuit_open: boolean
}

export interface StationStatus {
  station: {
    id: string | null
    name: string
    timezone: string
    latitude: number | null
    longitude: number | null
    software_version: string
    uptime_s: number
  }
  capture: CaptureStatus
  resampler: {
    backend: string
    backend_detail: string
    source_rate: number
    target_rate: number
    ratio: string
    input_frames: number
    output_frames: number
    delivery_deficit_frames: number
    delivery_deficit_ms: number
    delivery_deficit_min: number
    delivery_deficit_max: number
    group_delay_frames: number
  } | null
  rings: { native: RingStatus | null; audible: RingStatus | null }
  levels: { native: LevelSample | null; audible: LevelSample | null; note: string }
  spectrograms: SpectrogramSpec[]
  segmenters: Array<{
    stream_kind: string
    sample_rate: number
    duration_s: number
    stride_s: number
    buffered_frames: number
    buffered_s: number
    windows_emitted: number
    resets: number
    consumers: string[]
  }>
  leases: {
    outstanding: number
    granted: number
    released: number
    expired: number
    consumers: string[]
  }
  detectors: DetectorStatus[]
  normaliser: { normalised: number; duplicates_suppressed: number; claim_violations: number }
  clips: {
    requested: number
    written: number
    skipped_low_score: number
    skipped_plugin_not_clipped: number
    skipped_rate_limited: number
    skipped_disk_guard: number
    failed_not_in_ring: number
    failed_io: number
    bytes_written: number
    writes_last_minute: number
    policy: Record<string, unknown>
    disk_guard_active: string | null
  }
  storage: {
    clip_dir: string
    clip_count: number
    clip_bytes: number
    disk_total_bytes: number
    disk_free_bytes: number
    disk_used_ratio: number
  }
  live_audio: {
    sample_rate: number
    chunk_ms: number
    chunk_frames: number
    listeners: number
    chunks_published: number
    last_peak: number
  }
  bus: {
    published: number
    subscribers: number
    history: number
    per_subscriber: Array<{ label: string; queued: number; delivered: number; dropped: number }>
  }
  persistence: { written: number; queued: number; dropped: number; failures: number }
}

export interface MediaRef {
  id: string
  kind: string
  role: string
  /** Short human-readable rendering description, e.g. "x10 time expansion". */
  description?: string | null
  /** Provenance of any processing applied. See audio/ultrasound.py. */
  detail?: Record<string, unknown>
  sample_rate: number
  duration_s?: number
  byte_length: number
  sha256: string
  url: string
}

export interface Detection {
  id: string
  detector: {
    plugin_id: string
    plugin_version: string
    model_id: string
    model_version: string
    model_sha256?: string | null
  }
  stream_id: string
  window_id: string
  event_start_utc: string
  event_end_utc: string
  duration_s: number
  source_start_frame: number
  source_end_frame: number
  label: string | null
  display_name: string
  /** Presentational only: frequency + candidate species for a bat pass, e.g.
   *  "45 kHz · common pipistrelle?". Null for anything that is not a bat pass.
   *  Never an identification — see docs/adr/ADR-013. */
  title_hint?: string | null
  common_name: string | null
  scientific_name: string | null
  canonical_taxon_id: string | null
  rank: string | null
  taxonomic_group: string
  score: number
  calibrated_probability: number | null
  peak_frequency_hz: number | null
  /** Small derived markers computed from native_result, present even where
   *  native_result itself is not (list responses). */
  flags?: { feeding_buzz: boolean; withdrawn?: boolean }
  /** True when a plausibility review has withdrawn this claim (ADR-042). The row
   *  is deliberately still returned — withdrawn, not deleted — so it must be
   *  rendered with its marker and never as a plain observation. */
  withdrawn?: boolean
  /** The reviewer's recomputed prior, threshold, reason and timestamp, verbatim.
   *  Null unless `withdrawn`. */
  withdrawal?: Record<string, unknown> | null
  /** Present on `GET /detections/{id}` and on live WebSocket frames; omitted from
   *  `GET /detections` list rows unless `include_native=true` was requested. */
  native_result?: Record<string, unknown>
  media: MediaRef[]
}

export interface Envelope {
  schema_version: string
  event_id: string
  event_type: string
  occurred_at: string
  station_id: string | null
  data: Record<string, unknown>
}

/** One or more spectrogram columns decoded from a binary live frame. */
export interface ColumnBatch {
  channel: number
  bins: number
  columns: number
  firstUtcS: number
  /** Row-major, `columns` rows of `bins` bytes. */
  data: Uint8Array
}
