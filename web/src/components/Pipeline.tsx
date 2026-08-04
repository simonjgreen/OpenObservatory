/** Debug panels: the pipeline's own state, stage by stage.
 *
 *  This is the half of the UI the product dashboard would hide (ADR-011). It exists
 *  so that "no detections" can be told apart from "capture stalled", "detector
 *  behind", "windows being dropped" and "clips being refused" — each of which looks
 *  identical from the species list alone.
 */

import { useState } from 'react'
import type { DetectorStatus, Envelope, StationStatus } from '../types'

function bytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(0)} kB`
  return `${value} B`
}

function Bar({ fraction, tone = 'ok' }: { fraction: number; tone?: string }) {
  return (
    <div className={`minibar tone-${tone}`}>
      <span style={{ width: `${Math.max(0, Math.min(1, fraction)) * 100}%` }} />
    </div>
  )
}

export function CapturePanel({ status }: { status: StationStatus }) {
  const capture = status.capture
  const resampler = status.resampler
  const native = status.rings.native
  const audible = status.rings.audible
  const continuity = capture.continuity_ratio ?? 0

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Capture &amp; derivation</h2>
        <span className={`chip ${capture.is_live_hardware ? 'ok' : 'danger'}`}>
          {capture.is_live_hardware ? 'live hardware' : capture.source_kind ?? 'no source'}
        </span>
      </header>

      <dl className="kv">
        <div>
          <dt>device</dt>
          <dd title={capture.device_key ?? ''}>{capture.device_label ?? '—'}</dd>
        </div>
        <div>
          <dt>negotiated</dt>
          <dd className="mono">
            {capture.sample_rate ? `${(capture.sample_rate / 1000).toFixed(0)} kHz` : '—'}{' '}
            {capture.sample_format} {capture.channels}ch
          </dd>
        </div>
        <div>
          <dt title="Frames captured divided by frames elapsed monotonic time implies, measured from frame zero">
            continuity
          </dt>
          <dd className="mono">
            {(continuity * 100).toFixed(4)}%
            <Bar fraction={continuity} tone={continuity > 0.9995 ? 'ok' : 'warn'} />
          </dd>
        </div>
        <div>
          <dt>frames</dt>
          <dd className="mono">{capture.frames.toLocaleString()}</dd>
        </div>
        <div>
          <dt title="Gaps detected as a step in the frames-behind-wall-clock figure">
            gaps / missing
          </dt>
          <dd className={`mono ${capture.discontinuities > 0 ? 'warn-text' : ''}`}>
            {capture.discontinuities} / {capture.estimated_missing_frames.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt title="ALSA overrun count reported by the driver">overruns</dt>
          <dd className={`mono ${(capture.overruns ?? 0) > 0 ? 'warn-text' : ''}`}>
            {capture.overruns ?? 0}
          </dd>
        </div>
        <div>
          <dt title="The device runs on its own crystal; this is its measured offset from nominal, with gap losses excluded">
            clock offset
          </dt>
          <dd className="mono">
            {capture.rate_offset_ppm !== null ? `${capture.rate_offset_ppm.toFixed(1)} ppm` : '—'}
          </dd>
        </div>
        <div>
          <dt title="Seconds of CPU spent in the per-block hot path per second of audio. Budget is under 0.35 for capture plus resampling.">
            hot path CPU
          </dt>
          <dd className="mono">
            {capture.hot_path_cpu_ratio !== null
              ? `${(capture.hot_path_cpu_ratio * 100).toFixed(1)}%`
              : '—'}
            <Bar
              fraction={(capture.hot_path_cpu_ratio ?? 0) / 0.35}
              tone={(capture.hot_path_cpu_ratio ?? 0) < 0.35 ? 'ok' : 'warn'}
            />
          </dd>
        </div>
        <div>
          <dt>block age</dt>
          <dd className={`mono ${(capture.block_age_s ?? 0) > 1 ? 'warn-text' : ''}`}>
            {capture.block_age_s !== null ? `${capture.block_age_s.toFixed(2)} s` : '—'}
          </dd>
        </div>
        <div>
          <dt>restarts</dt>
          <dd className="mono">
            {capture.stream_restarts} ({capture.open_failures} open failures)
          </dd>
        </div>
      </dl>

      {resampler && (
        <div className="subsection">
          <h3>
            Resampler <span className="dim mono">{resampler.ratio}</span>
          </h3>
          <dl className="kv compact">
            <div>
              <dt>backend</dt>
              <dd className="mono" title={resampler.backend_detail}>
                {resampler.backend}
              </dd>
            </div>
            <div>
              <dt title="Output frame n maps to native frame n*src/dst. Verified zero on this device.">
                group delay
              </dt>
              <dd className="mono">{resampler.group_delay_frames} frames</dd>
            </div>
            <div>
              <dt title="Frames still inside the filter. libsoxr emits ragged chunks, so this oscillates within a bounded band — it is delivery latency, not drift. Timestamps come from frame indices, so it cannot become a timing error.">
                delivery latency
              </dt>
              <dd className="mono">
                {resampler.delivery_deficit_ms.toFixed(2)} ms
                <span className="dim">
                  {' '}
                  (band {resampler.delivery_deficit_min}–{resampler.delivery_deficit_max})
                </span>
              </dd>
            </div>
          </dl>
        </div>
      )}

      <div className="subsection">
        <h3>Ring buffers</h3>
        {[
          ['native', native],
          ['audible', audible],
        ].map(([name, ring]) =>
          ring && typeof ring !== 'string' ? (
            <div className="ring-row" key={name as string}>
              <span className="ring-name">{name as string}</span>
              <Bar fraction={ring.fill_ratio} />
              <span className="mono dim">
                {ring.held_seconds.toFixed(0)}/{ring.capacity_seconds.toFixed(0)}s ·{' '}
                {bytes(ring.estimated_bytes)}
              </span>
              <span
                className={`mono ${ring.extraction_misses > 0 ? 'warn-text' : 'dim'}`}
                title="Evidence extractions that could not be served because the audio had aged out"
              >
                {ring.extraction_misses} misses
              </span>
            </div>
          ) : null,
        )}
      </div>
    </section>
  )
}

export function DetectorPanel({ detectors }: { detectors: DetectorStatus[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Detectors</h2>
      </header>
      <div className="detector-list">
        {detectors.map((detector) => {
          const dropped = detector.windows_dropped_queue_full + detector.windows_dropped_stale
          const open = expanded === detector.plugin_id
          return (
            <div
              key={detector.plugin_id}
              className={`detector state-${detector.state} ${open ? 'open' : ''}`}
            >
              <button
                className="detector-head"
                onClick={() => setExpanded(open ? null : detector.plugin_id)}
              >
                <span className={`status-dot ${detector.state}`} />
                <span className="detector-name">{detector.plugin_id}</span>
                <span className="mono dim">
                  {detector.window.duration_s}s/{detector.window.stride_s}s ·{' '}
                  {detector.window.stream_kind}
                </span>
                <span className="grow" />
                <span className="mono" title="Windows analysed">
                  {detector.windows_analysed.toLocaleString()}
                </span>
                <span
                  className={`mono ${dropped > 0 ? 'warn-text' : 'dim'}`}
                  title="Windows dropped: queue full or past the delivery deadline"
                >
                  −{dropped}
                </span>
                <span className="mono" title="Detections emitted">
                  {detector.detections_emitted.toLocaleString()}
                </span>
                <span
                  className="mono dim"
                  title="Realtime factor: window duration divided by mean analysis time. Above 1 means it keeps up."
                >
                  {detector.realtime_factor ? `${detector.realtime_factor.toFixed(0)}×` : '—'}
                </span>
              </button>

              {open && (
                <div className="detector-detail">
                  <p className="claim">{detector.claim}</p>
                  <dl className="kv compact">
                    <div>
                      <dt>model</dt>
                      <dd className="mono">
                        {detector.model_id} {detector.model_version}
                      </dd>
                    </div>
                    <div>
                      <dt>licence</dt>
                      <dd>
                        {detector.licence_url ? (
                          <a href={detector.licence_url} target="_blank" rel="noreferrer">
                            {detector.licence_name}
                          </a>
                        ) : (
                          detector.licence_name
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>score meaning</dt>
                      <dd>
                        {detector.calibrated
                          ? 'calibrated probability'
                          : 'uncalibrated model score'}
                      </dd>
                    </div>
                    <div>
                      <dt>queue</dt>
                      <dd className="mono">
                        {detector.queue_depth}/{detector.queue_capacity}
                        <Bar
                          fraction={detector.queue_depth / Math.max(1, detector.queue_capacity)}
                          tone={detector.queue_depth > detector.queue_capacity / 2 ? 'warn' : 'ok'}
                        />
                      </dd>
                    </div>
                    <div>
                      <dt>runtime p95</dt>
                      <dd className="mono">
                        {detector.p95_runtime_ms ? `${detector.p95_runtime_ms.toFixed(1)} ms` : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt title="now minus window end for the last analysed window">lag</dt>
                      <dd className="mono">
                        {detector.lag_s !== null ? `${detector.lag_s.toFixed(2)} s` : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>failures</dt>
                      <dd className={`mono ${detector.failures > 0 ? 'warn-text' : ''}`}>
                        {detector.failures}
                        {detector.circuit_open && ' (circuit open)'}
                      </dd>
                    </div>
                  </dl>
                  {detector.detail && <p className="detail-text">{detector.detail}</p>}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

export function StoragePanel({ status }: { status: StationStatus }) {
  const { clips, storage, persistence, leases, normaliser, bus, live_audio } = status
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Evidence, storage &amp; buses</h2>
      </header>
      <dl className="kv compact">
        <div>
          <dt>clips written</dt>
          <dd className="mono">
            {clips.written.toLocaleString()} · {bytes(clips.bytes_written)}
          </dd>
        </div>
        <div>
          <dt title="Detections deliberately not clipped: the activity detector fires several times a second and clipping it filled the disk at 640 GB/day when measured">
            clips declined
          </dt>
          <dd className="mono dim">
            {clips.skipped_plugin_not_clipped.toLocaleString()} policy ·{' '}
            {clips.skipped_rate_limited} rate ·{' '}
            {clips.skipped_low_score} score
          </dd>
        </div>
        <div>
          <dt>clip rate</dt>
          <dd className="mono">
            {clips.writes_last_minute}/min limit {String(clips.policy.max_per_minute)}
          </dd>
        </div>
        <div>
          <dt>clip store</dt>
          <dd className="mono">
            {storage.clip_count.toLocaleString()} files · {bytes(storage.clip_bytes)}
          </dd>
        </div>
        <div>
          <dt>disk free</dt>
          <dd className="mono">
            {bytes(storage.disk_free_bytes)}
            <Bar
              fraction={storage.disk_used_ratio}
              tone={storage.disk_used_ratio > 0.9 ? 'warn' : 'ok'}
            />
          </dd>
        </div>
        {clips.disk_guard_active && (
          <div>
            <dt className="warn-text">disk guard</dt>
            <dd className="warn-text">{clips.disk_guard_active}</dd>
          </div>
        )}
        <div>
          <dt>detections stored</dt>
          <dd className={`mono ${persistence.failures > 0 ? 'warn-text' : ''}`}>
            {persistence.written.toLocaleString()} · {persistence.queued} queued ·{' '}
            {persistence.dropped} dropped · {persistence.failures} failed
          </dd>
        </div>
        <div>
          <dt title="Duplicate detections from overlapping windows, suppressed by the normaliser">
            duplicates suppressed
          </dt>
          <dd className="mono">{normaliser.duplicates_suppressed.toLocaleString()}</dd>
        </div>
        <div>
          <dt title="A detector emitting output its metadata forbids, e.g. the activity detector naming a species. Must always be zero.">
            claim violations
          </dt>
          <dd className={`mono ${normaliser.claim_violations > 0 ? 'warn-text' : ''}`}>
            {normaliser.claim_violations}
          </dd>
        </div>
        <div>
          <dt title="Outstanding transient-asset leases on in-flight windows">window leases</dt>
          <dd className="mono">
            {leases.outstanding} open · {leases.granted.toLocaleString()} granted ·{' '}
            <span className={leases.expired > 0 ? 'warn-text' : ''}>{leases.expired} expired</span>
          </dd>
        </div>
        <div>
          <dt>event bus</dt>
          <dd className="mono">
            {bus.published.toLocaleString()} published · {bus.subscribers} subscribers ·{' '}
            <span
              className={
                bus.per_subscriber.some((s) => s.dropped > 0) ? 'warn-text' : 'dim'
              }
            >
              {bus.per_subscriber.reduce((total, s) => total + s.dropped, 0)} dropped
            </span>
          </dd>
        </div>
        <div>
          <dt>live audio</dt>
          <dd className="mono">
            {live_audio.listeners} listener(s) · {live_audio.chunk_ms} ms chunks
          </dd>
        </div>
      </dl>
    </section>
  )
}

const EVENT_TONE: Record<string, string> = {
  'detection.created': 'ok',
  'capture.gap': 'danger',
  'capture.started': 'ok',
  'capture.stopped': 'warn',
  'window.dropped': 'warn',
  'health.event': 'danger',
  'clip.written': 'dim',
  'detector.state': 'warn',
}

export function EventLog({
  events,
  localTimeZone,
}: {
  events: Envelope[]
  localTimeZone: string
}) {
  const [filter, setFilter] = useState('')
  const [paused, setPaused] = useState(false)
  const [frozen, setFrozen] = useState<Envelope[]>([])

  const shown = paused ? frozen : events
  const filtered = filter
    ? shown.filter(
        (event) =>
          event.event_type.includes(filter) ||
          JSON.stringify(event.data).toLowerCase().includes(filter.toLowerCase()),
      )
    : shown

  const format = new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: localTimeZone,
  })

  return (
    <section className="panel events">
      <header className="panel-head">
        <h2>Event stream</h2>
        <input
          className="filter"
          placeholder="filter…"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <button
          className={paused ? 'on' : ''}
          onClick={() => {
            if (!paused) setFrozen(events)
            setPaused(!paused)
          }}
        >
          {paused ? 'resume' : 'pause'}
        </button>
      </header>
      <ol className="event-list">
        {filtered.map((event) => (
          <li key={event.event_id} className={`event tone-${EVENT_TONE[event.event_type] ?? 'dim'}`}>
            <span className="mono time">{format.format(Date.parse(event.occurred_at))}</span>
            <span className="type">{event.event_type}</span>
            <span className="summary mono">{summarise(event)}</span>
          </li>
        ))}
        {filtered.length === 0 && <li className="empty">no matching events</li>}
      </ol>
    </section>
  )
}

function summarise(event: Envelope): string {
  const data = event.data as Record<string, unknown>
  switch (event.event_type) {
    case 'detection.created':
      return `${data.display_name} ${(Number(data.score) * 100).toFixed(0)} (${
        (data.detector as Record<string, unknown> | undefined)?.plugin_id ?? '?'
      })`
    case 'capture.gap':
      return `${data.reason} — ${data.estimated_seconds}s, ${data.estimated_missing_frames} frames`
    case 'capture.started':
      return `${data.device_label} @ ${data.sample_rate} Hz${data.synthetic ? ' (SYNTHETIC)' : ''}`
    case 'capture.stopped':
      return `${data.reason} after ${Number(data.frames).toLocaleString()} frames`
    case 'capture.levels':
      return `rms ${data.rms_dbfs} peak ${data.peak_dbfs} clipped ${data.clipped_samples}`
    case 'window.dropped':
      return `${data.plugin_id}: ${data.reason} (queue ${data.queue_depth})`
    case 'clip.written':
      return `${data.kind} ${data.duration_s}s @ ${data.sample_rate} Hz`
    case 'detector.state':
      return `${data.plugin_id} → ${data.state} ${data.detail ?? ''}`
    case 'health.event':
      return `${data.severity} ${data.event_type}`
    case 'station.status':
      return `capture ${(data.capture as Record<string, unknown> | undefined)?.state ?? '?'}`
    default:
      return JSON.stringify(data).slice(0, 140)
  }
}
