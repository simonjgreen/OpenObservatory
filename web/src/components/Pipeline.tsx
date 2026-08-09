/** Debug panels: the pipeline's own state, stage by stage.
 *
 *  This is the half of the UI the product dashboard would hide (ADR-011). It exists
 *  so that "no detections" can be told apart from "capture stalled", "detector
 *  behind", "windows being dropped" and "clips being refused" — each of which looks
 *  identical from the species list alone.
 */

import { useState } from 'react'
import { formatDetectionTitleText, type DetectionTitleSource } from './detectionTitle'
import type { CaptureStatus, DetectorStatus, Envelope, StationStatus } from '../types'

function bytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(0)} kB`
  return `${value} B`
}

/** Seconds as something a person can read: "11.9 h", "3.4 m", "42 s". */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—'
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)} h`
  if (seconds >= 60) return `${(seconds / 60).toFixed(1)} m`
  return `${seconds.toFixed(0)} s`
}

/** Lost audio, where sub-second amounts matter and "0" should read as none.
 *  Deliberately more precise than `formatDuration`: the difference between
 *  0 s and 0.06 s is the difference between a clean run and a real gap. */
export function formatDeficit(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'none'
  if (seconds >= 60) return `${(seconds / 60).toFixed(1)} m`
  if (seconds >= 1) return `${seconds.toFixed(2)} s`
  return `${(seconds * 1000).toFixed(0)} ms`
}

/** What `expected_frames - frames` is actually made of.
 *
 *  This row used to be labelled "audio lost" and showed the raw deficit. That
 *  label was not true, and the charter's honesty constraint says a number shown
 *  to a human must mean what its label says. The deficit is four things added
 *  together, and on this station only one of them is lost audio (ADR-046):
 *
 *  1. **Sampling phase.** `frames` advances in whole blocks (38,400 frames =
 *     100 ms at 384 kHz) while `expected_frames` advances continuously, so the
 *     raw deficit sawtooths across a whole block *while nothing at all is
 *     wrong*. Measured on the station over a clean run with zero gaps, zero
 *     overruns and zero estimated loss, the raw figure ranged −162 ms to
 *     +133 ms. A single reading of it therefore carries about ±50 ms of pure
 *     artefact — the same order as the numbers this row was being read for.
 *  2. **Crystal drift.** This AudioMoth runs about 50.4 ppm slow against the
 *     host, so it legitimately delivers fewer frames than nominal wall time
 *     implies: 0.18 s per hour, 4.4 s per day, forever, with nothing lost. Over
 *     a night that term alone reaches 2 s and looks exactly like a slow leak.
 *  3. **Anchor bias**, a constant few milliseconds from where frame zero is
 *     pinned.
 *  4. **Lost audio** — the only term anybody wants — which is what
 *     `estimated_missing_frames` measures, being by construction the part of
 *     the deficit that never came back.
 *
 *  So `lost` is reported from the estimator, and the deficit is reported
 *  separately as what it is, with its drift term named rather than hidden.
 */
export interface DeficitBreakdown {
  /** Confirmed lost audio: deficit steps that never came back (ADR-039). */
  lostSeconds: number
  /** Raw `expected_frames - frames`. Loss + drift + anchor + sampling phase. */
  deficitSeconds: number
  /** The part of the deficit the measured crystal offset accounts for. */
  driftSeconds: number
  /** Deficit less loss and drift: anchor bias plus up to one block of phase. */
  residualSeconds: number
  /** Half a block — the amount a single reading of the deficit is uncertain by. */
  phaseSeconds: number
}

export function describeDeficit(capture: CaptureStatus): DeficitBreakdown | null {
  const rate = capture.sample_rate
  if (!rate || capture.expected_frames === null) return null
  const lostSeconds = capture.estimated_missing_seconds ?? 0
  const deficitSeconds = (capture.expected_frames - capture.frames) / rate
  // Elapsed since frame zero, taken from the same clock the deficit is built on
  // rather than from `started_utc`, so the two cannot drift apart.
  const elapsedSeconds = capture.expected_frames / rate
  // `rate_offset_ppm` is negative when the device runs slow, and a slow device
  // is what *creates* deficit, hence the sign flip.
  const driftSeconds = Math.max(0, -(capture.rate_offset_ppm ?? 0) * 1e-6 * elapsedSeconds)
  const blockFrames = Number(capture.stream_detail?.block_frames)
  return {
    lostSeconds,
    deficitSeconds,
    driftSeconds,
    residualSeconds: deficitSeconds - driftSeconds - lostSeconds,
    phaseSeconds: Number.isFinite(blockFrames) && blockFrames > 0 ? blockFrames / rate / 2 : 0,
  }
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
  const deficit = describeDeficit(capture)

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
          {/* Duration, not the raw count. `frames` is an eleven-digit number
              (16,462,694,400 after twelve hours at 384 kHz) that no one can
              read at a glance; the count itself is a debugging primitive, so
              it lives in the tooltip where it is still available. */}
          <dt title={`${capture.frames.toLocaleString()} frames captured`}>captured</dt>
          <dd className="mono">{formatDuration(capture.frames / (capture.sample_rate || 1))}</dd>
        </div>
        <div>
          {/* Lost audio, and nothing else. This row showed the raw
              `expected_frames - frames` deficit until ADR-046, under this same
              label — and that deficit is loss plus crystal drift plus anchor
              bias plus up to a whole block of sampling phase. It read 0.104 s
              on a station that had lost nothing, and its drift term grows at
              0.18 s/hour forever. The estimator's figure is the one that means
              what this label says; see `describeDeficit` above. */}
          <dt title="Audio confirmed gone: deficit steps that never came back (ADR-039). Losses smaller than one ALSA period (10 ms) are below its resolution.">
            audio lost
          </dt>
          <dd className={`mono ${(deficit?.lostSeconds ?? 0) > 0 ? 'warn-text' : ''}`}>
            {deficit === null ? '—' : formatDeficit(deficit.lostSeconds)}
          </dd>
        </div>
        <div>
          {/* Reported separately, because it is a different claim: the stream
              is behind wall clock, which on a device with its own crystal is
              expected and is not a loss. Naming the drift term inline is what
              stops the row being read as a leak. */}
          <dt title="expected_frames - frames: how far the stream is behind what elapsed time at the NOMINAL rate implies. This is not lost audio — it is mostly the device's own crystal offset, plus up to one 100 ms block of sampling phase. Read 'audio lost' for loss.">
            behind clock
          </dt>
          <dd className="mono">
            {deficit === null ? (
              '—'
            ) : (
              <>
                {formatDeficit(deficit.deficitSeconds)}
                <span className="dim">
                  {' '}
                  ({formatDeficit(deficit.driftSeconds)} drift
                  {deficit.phaseSeconds > 0
                    ? `, ±${(deficit.phaseSeconds * 1000).toFixed(0)} ms phase`
                    : ''}
                  )
                </span>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt title="Discontinuities detected as a step in the frames-behind-wall-clock figure. A gap is not the same as lost audio: most are absorbed by the ALSA ring.">
            gaps
          </dt>
          <dd className={`mono ${capture.discontinuities > 0 ? 'warn-text' : ''}`}>
            {capture.discontinuities}
          </dd>
        </div>
        <div>
          <dt title="ALSA overruns reported by the driver, and late reads the ring absorbed at no cost (ADR-039). A late read is a scheduling stall, not lost recording.">
            overruns
          </dt>
          <dd className={`mono ${(capture.overruns ?? 0) > 0 ? 'warn-text' : ''}`}>
            {capture.overruns ?? 0}
            <span className="dim"> · {capture.late_reads ?? 0} late reads</span>
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

/** Is this event an unidentified acoustic event rather than an identification?
 *
 *  The activity detector fires several times a second, so these outnumber
 *  everything else by a wide margin and bury the events you are usually reading the
 *  stream for — capture gaps, detector state changes, clip writes, real detections.
 */
function isUnidentified(event: Envelope): boolean {
  return (
    event.event_type === 'detection.created' &&
    (event.data as Record<string, unknown>).taxonomic_group === 'acoustic_event'
  )
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
  // Hidden by default, for the same reason the suggestion list hides them.
  const [hideUnidentified, setHideUnidentified] = useState(true)

  const shown = paused ? frozen : events
  const hiddenCount = hideUnidentified ? shown.filter(isUnidentified).length : 0
  const visible = hideUnidentified ? shown.filter((event) => !isUnidentified(event)) : shown
  const filtered = filter
    ? visible.filter(
        (event) =>
          event.event_type.includes(filter) ||
          JSON.stringify(event.data).toLowerCase().includes(filter.toLowerCase()),
      )
    : visible

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
      <div className="events-controls">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={hideUnidentified}
            onChange={(event) => setHideUnidentified(event.target.checked)}
          />
          hide unidentified events
        </label>
        {/* Stated rather than silent: suppressed is not the same as absent, and a
            quiet stream should not be mistaken for a quiet garden. */}
        <span className="dim mono">
          {hiddenCount > 0 ? `${hiddenCount} hidden` : ''}
        </span>
      </div>
      <ol className="event-list">
        {filtered.map((event) => (
          <li key={event.event_id} className={`event tone-${EVENT_TONE[event.event_type] ?? 'dim'}`}>
            <span className="mono time">{format.format(Date.parse(event.occurred_at))}</span>
            <span className="type">{event.event_type}</span>
            <span className="summary mono">{summarise(event)}</span>
          </li>
        ))}
        {filtered.length === 0 && (
          <li className="empty">
            {hiddenCount > 0
              ? `nothing but unidentified events — ${hiddenCount} hidden`
              : 'no matching events'}
          </li>
        )}
      </ol>
    </section>
  )
}

function summarise(event: Envelope): string {
  const data = event.data as Record<string, unknown>
  switch (event.event_type) {
    case 'detection.created':
      return `${formatDetectionTitleText(data as unknown as DetectionTitleSource)} ${(
        Number(data.score) * 100
      ).toFixed(0)} (${
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
